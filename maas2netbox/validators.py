# Copyright (C) 2019  GRNET S.A.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import logging
from urllib.parse import urlparse

from maas.client.enum import NodeStatus

from maas2netbox import config
from maas2netbox.utils import ipmi, maas, netbox


class Validator(object):

    def __init__(self, use_maas=False):
        self.netbox_api = netbox.NetBoxAPI()
        self.netbox_nodes = self.sanitized_netbox_nodes()
        if use_maas:
            self.maas_nodes = self.sanitized_maas_nodes()
        else:
            self.maas_nodes = None

    def sanitized_netbox_nodes(self):
        return self.netbox_api.get_nodes()

    def sanitized_maas_nodes(self):
        nodes = maas.get_nodes()
        return [node for node in nodes if node.status in [
            NodeStatus.DEPLOYED, NodeStatus.READY]]

    def check_nodes(self):
        raise NotImplementedError()

    @staticmethod
    def get_hostname(url):
        """Get hostname from URL"""
        parsed = urlparse(url)

        return parsed.scheme and parsed.netloc or parsed.path


class SerialNumberValidator(Validator):

    def sanitized_maas_nodes(self):
        sanitized_nodes = super(
            SerialNumberValidator, self).sanitized_maas_nodes()
        node_dict = {}
        for node in sanitized_nodes:
            serial = maas.get_node_serial(node)
            node_dict[node.hostname] = serial

        return node_dict

    def check_nodes(self):
        logging.info('Check Serial Number declared at NetBox')
        nodes_with_errors = {}

        for node in self.netbox_nodes:
            try:
                maas_serial = self.maas_nodes[node.name.lower()]
                if maas_serial != node.serial:
                    logging.info(
                        'Node: {} NetBox Serial: {} MaaS Serial: {}'
                        .format(node.name, node.serial, maas_serial))
                    nodes_with_errors[node.id] = {
                        'current': node.serial,
                        'expected': maas_serial}
            except KeyError:
                continue

        return nodes_with_errors


class IPMIFieldValidator(Validator):

    def check_nodes(self):
        logging.info('Check IPMI web address declared at NetBox')
        nodes_with_errors = {}

        for node in self.netbox_nodes:
            try:
                if node.custom_fields['IPMI']:
                    declared_ipmi = node.custom_fields['IPMI']
                else:
                    logging.error(
                        'Node: {} Failure: No IPMI location declared'.format(
                            node.name))
                    continue
                ipmi_iface = self.netbox_api.get_node_ipmi_interface(
                    node.id)
                if ipmi_iface:
                    mac_address = ipmi_iface.mac_address
                    expected = 'https://{}.{}'.format(
                        ''.join(mac_address.split(':')), config.ipmi_dns_zone)
                    if mac_address == '00:00:00:00:00:00':
                        logging.error(
                            'Node: {} Failure: Erroneous IPMI MAC address '
                            'found on NetBox'.format(node.name))
                        continue
                    if declared_ipmi != expected:
                        logging.info(
                            'Node: {} Declared IPMI: {} Expected IPMI: {}'
                            .format(node.name, declared_ipmi, expected))
                        nodes_with_errors[node.id] = {
                            'current': declared_ipmi,
                            'expected': expected}
                else:
                    logging.error(
                        'Node: {} Declared IPMI: {} Failure: No IPMI '
                        'interface found'.format(node.name, declared_ipmi))
            except Exception as e:
                logging.error(
                    'An error occurred for node: {}'.format(node.name), e)

        return nodes_with_errors


class IPMIInterfaceValidator(Validator):

    def check_nodes(self):
        logging.info(
            'Check MAC Address of the IPMI interface declared at NetBox')
        nodes_with_errors = {}

        for node in self.netbox_nodes:
            try:
                if node.custom_fields['IPMI']:
                    declared_ipmi_address = self.get_hostname(
                        node.custom_fields['IPMI'])
                else:
                    logging.error(
                        'Node: {} Failure: No IPMI location declared'.format(
                            node.name))
                    continue
                actual_mac_address = ipmi.get_mac_address(
                    declared_ipmi_address, config.ipmi_username,
                    config.ipmi_password).upper().decode()
                if not actual_mac_address:
                    logging.error(
                        'Node: {} Failure: Could not fetch actual IPMI MAC '
                        'address'.format(node.name))
                    continue
                ipmi_interface = self.netbox_api.get_node_ipmi_interface(
                    node.id)
                if not ipmi_interface:
                    logging.error(
                        'Node: {} Failure: No IPMI interface found at NetBox'
                        .format(node.name))
                    continue
                declared_mac_address = ipmi_interface.mac_address
                if declared_mac_address != actual_mac_address:
                    logging.info(
                        'Node: {} Declared MAC Address: {} '
                        'Actual MAC Address: {}'
                        .format(node.name, declared_mac_address,
                                actual_mac_address))
                    nodes_with_errors[ipmi_interface.id] = {
                        'current': declared_mac_address,
                        'expected': actual_mac_address}
            except Exception as e:
                logging.error(
                    'An error occurred for node: {}'.format(node.name), e)

        return nodes_with_errors


class StatusValidator(Validator):

    def sanitized_maas_nodes(self):
        sanitized_nodes = super(
            StatusValidator, self).sanitized_maas_nodes()
        node_dict = {}
        for node in sanitized_nodes:
            node_dict[node.hostname.upper()] = node.status

        return node_dict

    def check_nodes(self):
        logging.info('Check inconsistency between NetBox and MaaS status')
        nodes_with_errors = {}

        for node in self.netbox_nodes:
            try:
                netbox_status = node.status.label
                node_status = self.maas_nodes[node.name]
                translated_status = config.STATUS_DICT[node_status]

                if translated_status and translated_status != netbox_status:
                    logging.info(
                        'Node: {} Declared Status: {} Actual Status: {}'
                        .format(node.name, netbox_status,
                                translated_status))
                    nodes_with_errors[node.id] = {
                        'current': netbox_status,
                        'expected': translated_status}
            except KeyError:
                continue

        return nodes_with_errors


class PrimaryIPv4Validator(Validator):

    def sanitized_maas_nodes(self):
        sanitized_nodes = super(
            PrimaryIPv4Validator, self).sanitized_maas_nodes()
        node_dict = {}
        for node in sanitized_nodes:
            try:
                bond = node.interfaces.get_by_name('bond0')
                address = bond.links[0].ip_address
                if address:
                    node_dict[node.hostname.upper()] = address
            except (KeyError, IndexError, AttributeError):
                continue

        return node_dict

    def check_nodes(self):
        logging.info('Check Primary IPv4 Address declared at NetBox')
        nodes_with_errors = {}

        for node in self.netbox_nodes:
            try:
                node_address = None if not node.primary_ip4 \
                    else node.primary_ip4.address.split('/')[0]
                if node_address != self.maas_nodes[node.name]:
                    logging.info(
                        'Node: {} Declared Primary IPv4: {} '
                        'Actual Primary IPv4: {}'
                        .format(node.name, node_address,
                                self.maas_nodes[node.name]))
                    nodes_with_errors[node.id] = {
                        'current': node.primary_ip4,
                        'expected': self.maas_nodes[node.name]}
            except KeyError:
                continue

        return nodes_with_errors


class InterfacesValidator(Validator):

    def sanitized_maas_nodes(self):
        sanitized_nodes = super(
            InterfacesValidator, self).sanitized_maas_nodes()
        node_dict = {}
        for node in sanitized_nodes:
            ifaces = maas.get_node_interfaces(node)
            node_dict[node.hostname.upper()] = ifaces

        return node_dict

    def check_nodes(self):
        logging.info('Get actual interfaces of node to be declared in NetBox')
        nodes_with_errors = {}

        for node in self.netbox_nodes:
            missing_ifaces = []
            try:
                netbox_node_ifaces = self.netbox_api.get_node_interfaces(
                    node.id)
                node_ifaces = self.maas_nodes[node.name]
                for iface in node_ifaces:
                    found = False
                    for netbox_iface in netbox_node_ifaces:
                        if (
                            iface['name'] == netbox_iface.name
                            and iface['mac_address']
                                == netbox_iface.mac_address):
                            found = True
                            break
                    if not found:
                        logging.error(
                            'Node: {} Missing Interface: {} ({}) ({})'.format(
                                node.name, iface['name'],
                                iface['mac_address'], iface['type']))
                        missing_ifaces.append(iface)
            except KeyError:
                pass
            if missing_ifaces:
                nodes_with_errors[node.id] = {
                    'current': [],
                    'expected': missing_ifaces
                }

        return nodes_with_errors


class FirmwareValidator(Validator):

    def check_nodes(self):
        logging.info('Check Firmware Versions declared at NetBox')
        nodes_with_errors = {}

        for node in self.netbox_nodes:
            try:
                custom_fields = node.custom_fields
                if custom_fields['IPMI']:
                    node_ip = self.get_hostname(custom_fields['IPMI'])
                else:
                    logging.error(
                        'Node: {} Failure: No IPMI location declared'.format(
                            node.name))
                    continue
                firmware_output = ipmi.get_firmware_versions(
                    node_ip, config.ipmi_username, config.ipmi_password)
                firmware_versions = ipmi.parse_firmware_versions(
                    firmware_output)
                if not firmware_versions:
                    logging.error(
                        'Node: {} Failure: Cannot communicate with node'
                        .format(node.name))
                    continue
                found_errors = False
                for firmware, version in firmware_versions.items():
                    if custom_fields[firmware] != version:
                        if not found_errors:
                            nodes_with_errors[node.id] = {
                                'current': {},
                                'expected': {}
                            }
                            logging.info('Node: {}'.format(node.name))
                            found_errors = True

                        logging.info(
                            'Firmware: {} Declared Version: {} '
                            'Actual Version: {}'
                            .format(
                                firmware, custom_fields[firmware], version))
                        nodes_with_errors[node.id]['current'][firmware] = \
                            custom_fields[firmware]
                        nodes_with_errors[node.id]['expected'][firmware] = \
                            version
            except Exception as e:
                logging.error(
                    'An error occurred for node: {}'.format(node.name), e)

        return nodes_with_errors


class PlatformValidator(Validator):

    def sanitized_maas_nodes(self):
        sanitized_nodes = super(
            PlatformValidator, self).sanitized_maas_nodes()
        node_dict = {}
        for node in sanitized_nodes:
            if node.osystem and node.distro_series:
                platform = '{}-{}'.format(
                    node.osystem, node.distro_series)
            else:
                platform = None
            node_dict[node.hostname.upper()] = platform

        return node_dict

    def check_nodes(self):
        logging.info('Check Platform declared at NetBox')
        nodes_with_errors = {}

        for node in self.netbox_nodes:
            try:
                declared_platform = node.platform
                if declared_platform:
                    declared_platform = declared_platform.slug
                if declared_platform != self.maas_nodes[node.name]:
                    logging.info(
                        'Node: {} Declared Platform: {} Expected Platform: {}'
                        .format(node.name, declared_platform,
                                self.maas_nodes[node.name]))
                    nodes_with_errors[node.id] = {
                        'current': declared_platform,
                        'expected': self.maas_nodes[node.name]
                    }
            except KeyError:
                continue

        return nodes_with_errors


class SwitchConnectionsValidator(Validator):

    def sanitized_maas_nodes(self):
        sanitized_nodes = super(
            SwitchConnectionsValidator, self).sanitized_maas_nodes()
        node_dict = {}
        for node in sanitized_nodes:
            node_dict[node.hostname.upper()] = node

        return node_dict

    def check_nodes(self):
        logging.info('Check Switch Connections declared at NetBox')
        for node in self.netbox_nodes:
            try:
                maas_node = self.maas_nodes[node.name]
            except KeyError:
                continue

            logging.info('Node: {}'.format(node.name))

            ifaces_details = maas.get_switch_connection_details(maas_node)
            for iface in ifaces_details:

                netbox_ifaces = self.netbox_api.get_node_interfaces(
                    node.id, iface['name'])
                if len(netbox_ifaces) == 0:
                    logging.error('Expected to find: {} {}'.format(
                        node.name, iface['name']))
                    continue
                elif len(netbox_ifaces) != 1:
                    logging.error('Interface problem')
                    continue
                else:
                    netbox_iface = netbox_ifaces[0]

                if netbox_iface.untagged_vlan:
                    expected = self.netbox_api.get_vlan_id(iface['vid'])
                    actual = netbox_iface.untagged_vlan.id
                    if expected != actual:
                        logging.error('Node Untagged Vlan Mismatch')
                        continue

                    if netbox_iface.lag:
                        netbox_lag_iface = self.netbox_api.get_node_interface(
                            netbox_iface.lag.id)
                        if expected != netbox_lag_iface.untagged_vlan.id:
                            logging.error('Node LAG Untagged Vlan Mismatch')
                            continue

                switch = self.netbox_api.get_node_by_name(iface['switch_name'])
                if not switch:
                    logging.error('Switch Device is missing')
                    continue

                switch_ports = self.netbox_api.get_node_interfaces(
                    switch.id, iface['switch_port'])
                if len(switch_ports) != 1:
                    logging.error('Switch port problem')
                    continue
                else:
                    switch_port = switch_ports[0]

                cable = self.netbox_api.get_cable(
                    netbox_iface.id, switch_port.id)
                if not cable:
                    logging.error('Cable is missing')
                    continue

                if iface['cable_color'] != cable.color:
                    logging.error('Cable color mismatch')


class ExperimentalValidator(Validator):

    def check_nodes(self):
        logging.info('Experimental Validator - Use it at will')
        for node in self.netbox_nodes:
            logging.info(
                'Node: {} Comments: {}'.format(node.name, node.comments))
