# Copyright 2012-2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""DHCP management module."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = [
    'configure_dhcp',
    ]

from django.conf import settings
from maasserver.enum import NODEGROUP_STATUS
from maasserver.models import Config
from netaddr import IPAddress
from provisioningserver.tasks import (
    restart_dhcp_server,
    stop_dhcp_server,
    write_dhcp_config,
    )


def get_interfaces_managed_by(nodegroup):
    """Return `NodeGroupInterface` objects for which `nodegroup` manages DHCP.

    Returns only interfaces for which MAAS is supposed to serve DHCP.
    If the node group is not accepted, an empty list will be returned.
    Interfaces whose DHCP is not managed are not returned in any case.
    """
    if nodegroup.status == NODEGROUP_STATUS.ACCEPTED:
        return nodegroup.get_managed_interfaces()

    return None


def configure_dhcp(nodegroup):
    """Write the DHCP configuration file and restart the DHCP server."""
    # Let's get this out of the way first up shall we?
    if not settings.DHCP_CONNECT:
        # For the uninitiated, DHCP_CONNECT is set, by default, to False
        # in all tests and True in non-tests.  This avoids unnecessary
        # calls to async tasks.
        return

    # Circular imports.
    from maasserver.dns import get_dns_server_address

    interfaces = get_interfaces_managed_by(nodegroup)
    if interfaces in [None, []]:
        # interfaces being None means the cluster isn't accepted: stop
        # the DHCP server in case it case started.
        # interfaces being [] means there is no interface configured: stop
        # the DHCP server;  Note that a config generated with this setup
        # would not be valid and would result in the DHCP
        # server failing with the error: "Not configured to listen on any
        # interfaces!."
        stop_dhcp_server.apply_async(queue=nodegroup.work_queue)
        return

    # Make sure this nodegroup has a key to communicate with the dhcp
    # server.
    nodegroup.ensure_dhcp_key()

    dns_server = get_dns_server_address(nodegroup)
    ntp_server = Config.objects.get_config("ntp_server")
    dhcp_subnet_configs = []
    dhcp_subnet_configs = [
        dict(
            subnet=unicode(
                IPAddress(interface.ip_range_low) &
                IPAddress(interface.subnet_mask)),
            subnet_mask=interface.subnet_mask,
            broadcast_ip=interface.broadcast_ip,
            interface=interface.interface,
            router_ip=interface.router_ip,
            dns_servers=dns_server,
            ntp_server=ntp_server,
            domain_name=nodegroup.name,
            ip_range_low=interface.ip_range_low,
            ip_range_high=interface.ip_range_high,
        )
        for interface in interfaces]

    reload_dhcp_server_subtask = restart_dhcp_server.subtask(
        options={'queue': nodegroup.work_queue})
    task_kwargs = dict(
        dhcp_subnets=dhcp_subnet_configs,
        omapi_key=nodegroup.dhcp_key,
        dhcp_interfaces=' '.join(
            [interface.interface for interface in interfaces]),
        callback=reload_dhcp_server_subtask,
    )
    write_dhcp_config.apply_async(
        queue=nodegroup.work_queue, kwargs=task_kwargs)
