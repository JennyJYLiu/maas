# Copyright 2012 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Test cases for dns.config"""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

__metaclass__ = type
__all__ = []

import os.path
import random

from celery.conf import conf
from maastesting.factory import factory
from maastesting.fakemethod import FakeMethod
from maastesting.matchers import (
    ContainsAll,
    MatchesAll,
    )
from maastesting.testcase import TestCase
from provisioningserver.dns import config
from provisioningserver.dns.config import (
    DNSConfig,
    DNSConfigFail,
    DNSZoneConfig,
    execute_rndc_command,
    generate_rndc,
    MAAS_NAMED_CONF_NAME,
    MAAS_NAMED_RNDC_CONF_NAME,
    MAAS_RNDC_CONF_NAME,
    setup_rndc,
    TEMPLATES_PATH,
    )
import tempita
from testtools.matchers import (
    Contains,
    EndsWith,
    FileContains,
    StartsWith,
    )


class TestRNDCUtilities(TestCase):

    def test_generate_rndc_returns_configurations(self):
        rndc_content, named_content = generate_rndc()
        # rndc_content and named_content look right.
        self.assertIn('# Start of rndc.conf', rndc_content)
        self.assertIn('controls {', named_content)
        # named_content does not include any comment.
        self.assertNotIn('\n#', named_content)

    def test_setup_rndc_writes_configurations(self):
        dns_conf_dir = self.make_dir()
        self.patch(conf, 'DNS_CONFIG_DIR', dns_conf_dir)
        setup_rndc()
        expected = (
            (MAAS_RNDC_CONF_NAME, '# Start of rndc.conf'),
            (MAAS_NAMED_RNDC_CONF_NAME, 'controls {'))
        for filename, content in expected:
            with open(os.path.join(dns_conf_dir, filename), "rb") as stream:
                conf_content = stream.read()
                self.assertIn(content, conf_content)

    def test_execute_rndc_command_executes_command(self):
        recorder = FakeMethod()
        fake_dir = factory.getRandomString()
        self.patch(config, 'check_call', recorder)
        self.patch(conf, 'DNS_CONFIG_DIR', fake_dir)
        command = factory.getRandomString()
        execute_rndc_command(command)
        rndc_conf_path = os.path.join(fake_dir, MAAS_RNDC_CONF_NAME)
        expected_command = ['rndc', '-c', rndc_conf_path, command]
        self.assertEqual((expected_command,), recorder.calls[0][0])


class TestDNSConfig(TestCase):
    """Tests for DNSConfig."""

    def test_DNSConfig_defaults(self):
        dnsconfig = DNSConfig()
        self.assertEqual(
            (
                os.path.join(TEMPLATES_PATH, 'named.conf.template'),
                os.path.join(conf.DNS_CONFIG_DIR, MAAS_NAMED_CONF_NAME)
            ),
            (dnsconfig.template_path, dnsconfig.target_path))

    def test_get_template_retrieves_template(self):
        dnsconfig = DNSConfig()
        template = dnsconfig.get_template()
        self.assertIsInstance(template, tempita.Template)
        self.assertThat(
            dnsconfig.template_path, FileContains(template.content))

    def test_render_template(self):
        dnsconfig = DNSConfig()
        random_content = factory.getRandomString()
        template = tempita.Template("{{test}}")
        rendered = dnsconfig.render_template(template, test=random_content)
        self.assertEqual(random_content, rendered)

    def test_render_template_raises_PXEConfigFail(self):
        dnsconfig = DNSConfig()
        template = tempita.Template("template: {{test}}")
        exception = self.assertRaises(
            DNSConfigFail, dnsconfig.render_template, template)
        self.assertIn("'test' is not defined", exception.message)

    def test_write_config_writes_config(self):
        target_dir = self.make_dir()
        self.patch(DNSConfig, 'target_dir', target_dir)
        zone_names = [factory.getRandomString()]
        reverse_zone_names = [factory.getRandomString()]
        dnsconfig = DNSConfig(
            zone_names=zone_names, reverse_zone_names=reverse_zone_names)
        dnsconfig.write_config()
        self.assertThat(
            os.path.join(target_dir, MAAS_NAMED_CONF_NAME),
            FileContains(
                matcher=ContainsAll(
                    [
                        'zone "%s"' % zone_names[0],
                        'zone "%s.rev"' % reverse_zone_names[0],
                        MAAS_NAMED_RNDC_CONF_NAME,
                    ])))

    def test_get_include_snippet_returns_snippet(self):
        target_dir = self.make_dir()
        self.patch(DNSConfig, 'target_dir', target_dir)
        dnsconfig = DNSConfig()
        snippet = dnsconfig.get_include_snippet()
        self.assertThat(
            snippet,
            MatchesAll(
                StartsWith('\n'),
                EndsWith('\n'),
                Contains(target_dir),
                Contains('include "%s"' % dnsconfig.target_path)))


class TestDNSZoneConfig(TestCase):
    """Tests for DNSZoneConfig."""

    def test_name_returns_zone_name(self):
        zone_name = factory.getRandomString()
        dnszoneconfig = DNSZoneConfig(zone_name)
        self.assertEqual(dnszoneconfig.name, '%s' % zone_name)

    def test_DNSZoneConfig_fields(self):
        zone_name = factory.getRandomString()
        dnszoneconfig = DNSZoneConfig(zone_name)
        self.assertEqual(
            (
                os.path.join(TEMPLATES_PATH, 'zone.template'),
                os.path.join(conf.DNS_CONFIG_DIR, 'zone.%s' % zone_name)
            ),
            (dnszoneconfig.template_path, dnszoneconfig.target_path))

    def test_write_config_writes_zone_config(self):
        target_dir = self.make_dir()
        self.patch(DNSConfig, 'target_dir', target_dir)
        zone_name = factory.getRandomString()
        dnszoneconfig = DNSZoneConfig(zone_name)
        domain = factory.getRandomString()
        serial = random.randint(1, 100)
        hostname = factory.getRandomString()
        ip = factory.getRandomIPAddress()
        dnszoneconfig.write_config(
            domain=domain, serial=serial,
            hostname_ip_mapping={hostname: ip})
        self.assertThat(
            os.path.join(target_dir, 'zone.%s' % zone_name),
            FileContains(
                matcher=ContainsAll(
                    [
                        'IN  NS  %s.' % domain,
                        '%s IN A %s' % (hostname, ip),
                    ])))
