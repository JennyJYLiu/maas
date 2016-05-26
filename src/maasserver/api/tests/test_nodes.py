# Copyright 2013-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for the nodes API."""

__all__ = []

import http.client
import json
import random

from django.conf import settings
from django.core.urlresolvers import reverse
from django.http import QueryDict
from maasserver.api import nodes as nodes_module
from maasserver.api.utils import get_overridden_query_dict
from maasserver.enum import (
    INTERFACE_TYPE,
    NODE_STATUS,
    NODE_TYPE,
)
from maasserver.exceptions import MAASAPIValidationError
from maasserver.testing.api import APITestCase
from maasserver.testing.factory import factory
from maasserver.utils import ignore_unused
from maasserver.utils.orm import reload_object
from maastesting.djangotestcase import count_queries


class AnonymousIsRegisteredAPITest(APITestCase.ForAnonymous):

    def test_is_registered_returns_True_if_node_registered(self):
        mac_address = factory.make_mac_address()
        factory.make_Interface(
            INTERFACE_TYPE.PHYSICAL, mac_address=mac_address)
        response = self.client.get(
            reverse('nodes_handler'),
            {'op': 'is_registered', 'mac_address': mac_address})
        self.assertEqual(
            (http.client.OK.value, "true"),
            (response.status_code,
             response.content.decode(settings.DEFAULT_CHARSET)))

    def test_is_registered_normalizes_mac_address(self):
        # These two non-normalized MAC addresses are the same.
        non_normalized_mac_address = 'AA-bb-cc-dd-ee-ff'
        non_normalized_mac_address2 = 'aabbccddeeff'
        factory.make_Interface(
            INTERFACE_TYPE.PHYSICAL, mac_address=non_normalized_mac_address)
        response = self.client.get(
            reverse('nodes_handler'),
            {
                'op': 'is_registered',
                'mac_address': non_normalized_mac_address2
            })
        self.assertEqual(
            (http.client.OK.value, "true"),
            (response.status_code,
             response.content.decode(settings.DEFAULT_CHARSET)))

    def test_is_registered_returns_False_if_node_not_registered(self):
        mac_address = factory.make_mac_address()
        response = self.client.get(
            reverse('nodes_handler'),
            {'op': 'is_registered', 'mac_address': mac_address})
        self.assertEqual(
            (http.client.OK.value, "false"),
            (response.status_code,
             response.content.decode(settings.DEFAULT_CHARSET)))


def extract_system_ids(parsed_result):
    """List the system_ids of the nodes in `parsed_result`."""
    return [node.get('system_id') for node in parsed_result]


def extract_system_ids_from_nodes(nodes):
    return [node.system_id for node in nodes]


class RequestFixture:
    def __init__(self, dict, fields):
        self.user = factory.make_User()
        self.GET = get_overridden_query_dict(dict, QueryDict(''), fields)


class TestFilteredNodesListFromRequest(APITestCase.ForUser):

    def test_node_list_with_id_returns_matching_nodes(self):
        # The "list" operation takes optional "id" parameters.  Only
        # nodes with matching ids will be returned.
        ids = [factory.make_Node().system_id for _ in range(3)]
        matching_id = ids[0]
        query = RequestFixture({'id': [matching_id]}, 'id')
        node_list = nodes_module.filtered_nodes_list_from_request(query)

        self.assertItemsEqual(
            [matching_id],
            extract_system_ids_from_nodes(node_list))

    def test_node_list_with_nonexistent_id_returns_empty_list(self):
        # Trying to list a nonexistent node id returns a list containing
        # no nodes -- even if other (non-matching) nodes exist.
        existing_id = factory.make_Node().system_id
        nonexistent_id = existing_id + factory.make_string()
        query = RequestFixture({'id': [nonexistent_id]}, 'id')
        node_list = nodes_module.filtered_nodes_list_from_request(query)

        self.assertItemsEqual(
            [],
            extract_system_ids_from_nodes(node_list))

    def test_node_list_with_ids_orders_by_id(self):
        # Even when ids are passed to "list," nodes are returned in id
        # order, not necessarily in the order of the id arguments.
        all_nodes = [factory.make_Node() for _ in range(3)]
        system_ids = [node.system_id for node in all_nodes]
        random.shuffle(system_ids)

        query = RequestFixture({'id': list(system_ids)}, 'id')
        node_list = nodes_module.filtered_nodes_list_from_request(query)

        sorted_system_ids = [
            node.system_id
            for node in sorted(all_nodes, key=lambda node: node.id)
        ]
        self.assertSequenceEqual(
            sorted_system_ids,
            extract_system_ids_from_nodes(node_list))

    def test_node_list_with_some_matching_ids_returns_matching_nodes(self):
        # If some nodes match the requested ids and some don't, only the
        # matching ones are returned.
        existing_id = factory.make_Node().system_id
        nonexistent_id = existing_id + factory.make_string()

        query = RequestFixture({'id': [existing_id, nonexistent_id]}, 'id')
        node_list = nodes_module.filtered_nodes_list_from_request(query)

        self.assertItemsEqual(
            [existing_id],
            extract_system_ids_from_nodes(node_list))

    def test_node_list_with_hostname_returns_matching_nodes(self):
        # The list operation takes optional "hostname" parameters. Only nodes
        # with matching hostnames will be returned.
        nodes = [factory.make_Node() for _ in range(3)]
        matching_hostname = nodes[0].hostname
        matching_system_id = nodes[0].system_id

        query = RequestFixture({'hostname': [matching_hostname]}, 'hostname')
        node_list = nodes_module.filtered_nodes_list_from_request(query)

        self.assertItemsEqual(
            [matching_system_id],
            extract_system_ids_from_nodes(node_list))

    def test_node_list_with_macs_returns_matching_nodes(self):
        # The "list" operation takes optional "mac_address" parameters. Only
        # nodes with matching MAC addresses will be returned.
        interfaces = [
            factory.make_Interface(INTERFACE_TYPE.PHYSICAL)
            for _ in range(3)
        ]
        matching_mac = str(interfaces[0].mac_address)
        matching_system_id = interfaces[0].node.system_id

        query = RequestFixture({'mac_address': [matching_mac]}, 'mac_address')
        node_list = nodes_module.filtered_nodes_list_from_request(query)

        self.assertItemsEqual(
            [matching_system_id],
            extract_system_ids_from_nodes(node_list))

    def test_node_list_with_invalid_macs_returns_sensible_error(self):
        # If specifying an invalid MAC, make sure the error that's
        # returned is not a crazy stack trace, but something nice to
        # humans.
        bad_mac1 = '00:E0:81:DD:D1:ZZ'  # ZZ is bad.
        bad_mac2 = '00:E0:81:DD:D1:XX'  # XX is bad.
        ok_mac = str(
            factory.make_Interface(INTERFACE_TYPE.PHYSICAL).mac_address)
        mac_list = [bad_mac1, bad_mac2, ok_mac]

        query = RequestFixture({'mac_address': mac_list}, 'mac_address')
        expected_msg = [
            "Invalid MAC address(es): 00:E0:81:DD:D1:ZZ, 00:E0:81:DD:D1:XX"
        ]
        ex = self.assertRaises(
            MAASAPIValidationError,
            nodes_module.filtered_nodes_list_from_request,
            query)
        self.assertEqual(expected_msg, ex.messages)

    def test_node_list_with_agent_name_filters_by_agent_name(self):
        non_listed_node = factory.make_Node(
            agent_name=factory.make_name('agent_name'))
        ignore_unused(non_listed_node)
        agent_name = factory.make_name('agent-name')
        node = factory.make_Node(agent_name=agent_name)

        query = RequestFixture({'agent_name': agent_name}, 'agent_name')
        node_list = nodes_module.filtered_nodes_list_from_request(query)

        self.assertSequenceEqual(
            [node.system_id],
            extract_system_ids_from_nodes(node_list))

    def test_node_list_with_agent_name_filters_with_empty_string(self):
        factory.make_Node(agent_name=factory.make_name('agent-name'))
        node = factory.make_Node(agent_name='')

        query = RequestFixture({'agent_name': ''}, 'agent_name')
        node_list = nodes_module.filtered_nodes_list_from_request(query)

        self.assertSequenceEqual(
            [node.system_id],
            extract_system_ids_from_nodes(node_list))

    def test_node_list_without_agent_name_does_not_filter(self):
        nodes = [
            factory.make_Node(agent_name=factory.make_name('agent-name'))
            for _ in range(3)]

        query = RequestFixture({}, '')
        node_list = nodes_module.filtered_nodes_list_from_request(query)

        self.assertSequenceEqual(
            [node.system_id for node in nodes],
            extract_system_ids_from_nodes(node_list))

    def test_node_lists_list_devices(self):
        machines = [
            factory.make_Node(agent_name=factory.make_name('agent-name'))
            for _ in range(3)]
        # Create devices.
        devices = [
            factory.make_Device()
            for _ in range(3)]

        query = RequestFixture({}, '')
        node_list = nodes_module.filtered_nodes_list_from_request(query)

        system_ids = extract_system_ids_from_nodes(node_list)
        self.assertEqual(
            [node.system_id for node in machines + devices],
            system_ids,
            "Node listing doesn't contain devices.")

    def test_node_list_with_zone_filters_by_zone(self):
        non_listed_node = factory.make_Node(
            zone=factory.make_Zone(name='twilight'))
        ignore_unused(non_listed_node)
        zone = factory.make_Zone()
        node = factory.make_Node(zone=zone)

        query = RequestFixture({'zone': zone.name}, 'zone')
        node_list = nodes_module.filtered_nodes_list_from_request(query)

        self.assertSequenceEqual(
            [node.system_id], extract_system_ids_from_nodes(node_list))

    def test_node_list_without_zone_does_not_filter(self):
        nodes = [factory.make_Node(zone=factory.make_Zone())
                 for _ in range(3)]

        query = RequestFixture({}, '')
        node_list = nodes_module.filtered_nodes_list_from_request(query)

        self.assertSequenceEqual(
            [node.system_id for node in nodes],
            extract_system_ids_from_nodes(node_list))


class TestNodesAPI(APITestCase.ForUser):
    """Tests for /api/2.0/nodes/."""

    def test_handler_path(self):
        self.assertEqual(
            '/api/2.0/nodes/', reverse('nodes_handler'))

    def test_GET_lists_nodes(self):
        # The api allows for fetching the list of Nodes.
        node1 = factory.make_Node()
        node2 = factory.make_Node(
            status=NODE_STATUS.ALLOCATED, owner=self.user)
        response = self.client.get(reverse('nodes_handler'))
        parsed_result = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))

        self.assertEqual(http.client.OK, response.status_code)
        self.assertItemsEqual(
            [node1.system_id, node2.system_id],
            extract_system_ids(parsed_result))

    def create_nodes(self, nodegroup, nb):
        for _ in range(nb):
            factory.make_Node(nodegroup=nodegroup, interface=True)

    def test_GET_nodes_issues_constant_number_of_queries(self):
        # XXX: GavinPanella 2014-10-03 bug=1377335
        self.skip("Unreliable; something is causing varying counts.")

        nodegroup = factory.make_NodeGroup()
        self.create_nodes(nodegroup, 10)
        num_queries1, response1 = count_queries(
            self.client.get, reverse('nodes_handler'))
        self.create_nodes(nodegroup, 10)
        num_queries2, response2 = count_queries(
            self.client.get, reverse('nodes_handler'))
        # Make sure the responses are ok as it's not useful to compare the
        # number of queries if they are not.
        self.assertEqual(
            [http.client.OK, http.client.OK, 10, 20],
            [
                response1.status_code,
                response2.status_code,
                len(extract_system_ids(json.loads(response1.content))),
                len(extract_system_ids(json.loads(response2.content))),
            ])
        self.assertEqual(num_queries1, num_queries2)

    def test_GET_without_nodes_returns_empty_list(self):
        # If there are no nodes to list, the "list" op still works but
        # returns an empty list.
        response = self.client.get(reverse('nodes_handler'))
        self.assertItemsEqual(
            [], json.loads(response.content.decode(settings.DEFAULT_CHARSET)))

    def test_GET_orders_by_id(self):
        # Nodes are returned in id order.
        nodes = [factory.make_Node() for counter in range(3)]
        response = self.client.get(reverse('nodes_handler'))
        parsed_result = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertSequenceEqual(
            [node.system_id for node in nodes],
            extract_system_ids(parsed_result))

    def test_GET_with_id_returns_matching_nodes(self):
        # The "list" operation takes optional "id" parameters.  Only
        # nodes with matching ids will be returned.
        ids = [factory.make_Node().system_id for counter in range(3)]
        matching_id = ids[0]
        response = self.client.get(reverse('nodes_handler'), {
            'id': [matching_id],
        })
        parsed_result = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertItemsEqual(
            [matching_id], extract_system_ids(parsed_result))

    def test_GET_list_with_nonexistent_id_returns_empty_list(self):
        # Trying to list a nonexistent node id returns a list containing
        # no nodes -- even if other (non-matching) nodes exist.
        existing_id = factory.make_Node().system_id
        nonexistent_id = existing_id + factory.make_string()
        response = self.client.get(reverse('nodes_handler'), {
            'id': [nonexistent_id],
        })
        self.assertItemsEqual([], json.loads(
            response.content.decode(settings.DEFAULT_CHARSET)))

    def test_GET_with_ids_orders_by_id(self):
        # Even when ids are passed to "list," nodes are returned in id
        # order, not necessarily in the order of the id arguments.
        ids = [factory.make_Node().system_id for counter in range(3)]
        response = self.client.get(reverse('nodes_handler'), {
            'id': list(reversed(ids)),
        })
        parsed_result = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertSequenceEqual(ids, extract_system_ids(parsed_result))

    def test_GET_with_some_matching_ids_returns_matching_nodes(self):
        # If some nodes match the requested ids and some don't, only the
        # matching ones are returned.
        existing_id = factory.make_Node().system_id
        nonexistent_id = existing_id + factory.make_string()
        response = self.client.get(reverse('nodes_handler'), {
            'id': [existing_id, nonexistent_id],
        })
        parsed_result = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertItemsEqual(
            [existing_id], extract_system_ids(parsed_result))

    def test_GET_with_hostname_returns_matching_nodes(self):
        # The list operation takes optional "hostname" parameters. Only nodes
        # with matching hostnames will be returned.
        nodes = [factory.make_Node() for _ in range(3)]
        matching_hostname = nodes[0].hostname
        matching_system_id = nodes[0].system_id
        response = self.client.get(reverse('nodes_handler'), {
            'hostname': [matching_hostname],
        })
        parsed_result = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertItemsEqual(
            [matching_system_id], extract_system_ids(parsed_result))

    def test_GET_with_macs_returns_matching_nodes(self):
        # The "list" operation takes optional "mac_address" parameters. Only
        # nodes with matching MAC addresses will be returned.
        interfaces = [
            factory.make_Interface(INTERFACE_TYPE.PHYSICAL)
            for _ in range(3)
        ]
        matching_mac = interfaces[0].mac_address
        matching_system_id = interfaces[0].node.system_id
        response = self.client.get(reverse('nodes_handler'), {
            'mac_address': [matching_mac],
        })
        parsed_result = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertItemsEqual(
            [matching_system_id], extract_system_ids(parsed_result))

    def test_GET_with_invalid_macs_returns_sensible_error(self):
        # If specifying an invalid MAC, make sure the error that's
        # returned is not a crazy stack trace, but something nice to
        # humans.
        bad_mac1 = '00:E0:81:DD:D1:ZZ'  # ZZ is bad.
        bad_mac2 = '00:E0:81:DD:D1:XX'  # XX is bad.
        ok_mac = str(
            factory.make_Interface(INTERFACE_TYPE.PHYSICAL).mac_address)
        response = self.client.get(reverse('nodes_handler'), {
            'mac_address': [bad_mac1, bad_mac2, ok_mac],
        })
        self.assertEqual(http.client.BAD_REQUEST, response.status_code)
        self.assertIn(
            "Invalid MAC address(es): 00:E0:81:DD:D1:ZZ, 00:E0:81:DD:D1:XX",
            response.content.decode(settings.DEFAULT_CHARSET))

    def test_GET_with_agent_name_filters_by_agent_name(self):
        non_listed_node = factory.make_Node(
            agent_name=factory.make_name('agent_name'))
        ignore_unused(non_listed_node)
        agent_name = factory.make_name('agent-name')
        node = factory.make_Node(agent_name=agent_name)
        response = self.client.get(reverse('nodes_handler'), {
            'agent_name': agent_name,
        })
        self.assertEqual(http.client.OK, response.status_code)
        parsed_result = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertSequenceEqual(
            [node.system_id], extract_system_ids(parsed_result))

    def test_GET_with_agent_name_filters_with_empty_string(self):
        factory.make_Node(agent_name=factory.make_name('agent-name'))
        node = factory.make_Node(agent_name='')
        response = self.client.get(reverse('nodes_handler'), {
            'agent_name': '',
        })
        self.assertEqual(http.client.OK, response.status_code)
        parsed_result = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertSequenceEqual(
            [node.system_id], extract_system_ids(parsed_result))

    def test_GET_without_agent_name_does_not_filter(self):
        nodes = [
            factory.make_Node(agent_name=factory.make_name('agent-name'))
            for _ in range(3)]
        response = self.client.get(reverse('nodes_handler'))
        self.assertEqual(http.client.OK, response.status_code)
        parsed_result = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertSequenceEqual(
            [node.system_id for node in nodes],
            extract_system_ids(parsed_result))

    def test_GET_shows_all_types(self):
        machines = [
            factory.make_Node(agent_name=factory.make_name('agent-name'))
            for _ in range(3)]
        # Create devices.
        devices = [
            factory.make_Node(node_type=NODE_TYPE.DEVICE)
            for _ in range(3)]
        rack_controllers = [
            factory.make_Node(agent_name=factory.make_name('agent-name'))
            for _ in range(3)]
        response = self.client.get(reverse('nodes_handler'))
        self.assertEqual(http.client.OK, response.status_code)
        parsed_result = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertItemsEqual(
            [node.system_id for node in machines + devices + rack_controllers],
            extract_system_ids(parsed_result),
            "Node listing doesn't contain all node types.")

    def test_GET_with_zone_filters_by_zone(self):
        non_listed_node = factory.make_Node(
            zone=factory.make_Zone(name='twilight'))
        ignore_unused(non_listed_node)
        zone = factory.make_Zone()
        node = factory.make_Node(zone=zone)
        response = self.client.get(reverse('nodes_handler'), {
            'zone': zone.name,
        })
        self.assertEqual(http.client.OK, response.status_code)
        parsed_result = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertSequenceEqual(
            [node.system_id], extract_system_ids(parsed_result))

    def test_GET_without_zone_does_not_filter(self):
        nodes = [
            factory.make_Node(zone=factory.make_Zone())
            for _ in range(3)]
        response = self.client.get(reverse('nodes_handler'))
        self.assertEqual(http.client.OK, response.status_code)
        parsed_result = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertSequenceEqual(
            [node.system_id for node in nodes],
            extract_system_ids(parsed_result))

    def test_POST_set_zone_sets_zone_on_nodes(self):
        self.become_admin()
        node = factory.make_Node()
        zone = factory.make_Zone()
        response = self.client.post(
            reverse('nodes_handler'),
            {
                'op': 'set_zone',
                'nodes': [node.system_id],
                'zone': zone.name
            })
        self.assertEqual(http.client.OK, response.status_code)
        node = reload_object(node)
        self.assertEqual(zone, node.zone)

    def test_POST_set_zone_does_not_affect_other_nodes(self):
        self.become_admin()
        node = factory.make_Node()
        original_zone = node.zone
        response = self.client.post(
            reverse('nodes_handler'),
            {
                'op': 'set_zone',
                'nodes': [factory.make_Node().system_id],
                'zone': factory.make_Zone().name
            })
        self.assertEqual(http.client.OK, response.status_code)
        node = reload_object(node)
        self.assertEqual(original_zone, node.zone)

    def test_POST_set_zone_requires_admin(self):
        node = factory.make_Node(owner=self.user)
        original_zone = node.zone
        response = self.client.post(
            reverse('nodes_handler'),
            {
                'op': 'set_zone',
                'nodes': [node.system_id],
                'zone': factory.make_Zone().name
            })
        self.assertEqual(http.client.FORBIDDEN, response.status_code)
        node = reload_object(node)
        self.assertEqual(original_zone, node.zone)

    def test_CREATE_disabled(self):
        response = self.client.post(reverse('nodes_handler'), {})
        self.assertEqual(http.client.BAD_REQUEST, response.status_code)

    def test_UPDATE_disabled(self):
        response = self.client.put(reverse('nodes_handler'), {})
        self.assertEqual(
            http.client.METHOD_NOT_ALLOWED, response.status_code)

    def test_DELETE_disabled(self):
        response = self.client.put(reverse('nodes_handler'), {})
        self.assertEqual(
            http.client.METHOD_NOT_ALLOWED, response.status_code)


class TestPowersMixin(APITestCase.ForUser):
    """Test the powers mixin."""

    def get_node_uri(self, node):
        """Get the API URI for `node`."""
        # Use the machine handler to test as that will always support all
        # power commands
        return reverse('machine_handler', args=[node.system_id])

    def test_GET_power_parameters_requires_admin(self):
        response = self.client.get(
            reverse('machines_handler'),
            {
                'op': 'power_parameters',
            })
        self.assertEqual(
            http.client.FORBIDDEN, response.status_code, response.content)

    def test_GET_power_parameters_without_ids_does_not_filter(self):
        self.become_admin()
        machines = [
            factory.make_Node(power_parameters={factory.make_string():
                                                factory.make_string()})
            for _ in range(0, 3)
        ]
        response = self.client.get(
            reverse('machines_handler'),
            {
                'op': 'power_parameters',
            })
        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        parsed = json.loads(response.content.decode(settings.DEFAULT_CHARSET))
        expected = {
            machine.system_id: machine.power_parameters
            for machine in machines
        }
        self.assertEqual(expected, parsed)

    def test_GET_power_parameters_with_ids_filters(self):
        self.become_admin()
        machines = [
            factory.make_Node(power_parameters={factory.make_string():
                                                factory.make_string()})
            for _ in range(0, 6)
        ]
        expected_machines = random.sample(machines, 3)
        response = self.client.get(
            reverse('machines_handler'),
            {
                'op': 'power_parameters',
                'id': [machine.system_id for machine in expected_machines],
            })
        self.assertEqual(
            http.client.OK, response.status_code, response.content)
        parsed = json.loads(response.content.decode(settings.DEFAULT_CHARSET))
        expected = {
            machine.system_id: machine.power_parameters
            for machine in expected_machines
        }
        self.assertEqual(expected, parsed)
