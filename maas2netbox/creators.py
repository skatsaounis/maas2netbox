import json
import logging

from maas.client import enum

from maas2netbox import config
from maas2netbox.utils import maas, netbox


class Creator(object):
    def __init__(self, data):
        if data:
            self.data = json.loads(data)

    @property
    def netbox_nodes(self):
        nodes = netbox.get_nodes_by_site(config.site_name)
        node_dict = {}
        for node in nodes:
            node_dict[node['name'].lower()] = node['id']
        return node_dict

    @property
    def maas_nodes(self):
        nodes = []
        maas_nodes = maas.get_nodes()
        for node in maas_nodes:
            if (
                node.hostname.startswith('lar')
                and node.status in [
                    enum.NodeStatus.DEPLOYED, enum.NodeStatus.READY]
            ):
                nodes.append(node)
        return nodes

    def create(self):
        raise NotImplementedError


class IPMIInterfaceCreator(Creator):

    @staticmethod
    def get_form_factor_value(form_factor_text):
        form_factor_value = None
        form_factors = netbox.get_form_factors()
        for form_factor in form_factors:
            if form_factor['label'] == form_factor_text:
                form_factor_value = form_factor['value']
                break
        return form_factor_value

    def create(self):
        interface_data = {
            'name': ''.join(self.data['mac_address'].split(':')),
            'form_factor': self.get_form_factor_value(
                self.data['form_factor']),
            'device': self.data['node'],
            'enabled': True,
            'mtu': 1500,
            'mac_address': self.data['mac_address'],
            'mgmt_only': True,
            'mode': 100
        }
        netbox.create_interface(interface_data)


class VirtualInterfacesCreator(Creator):

    @staticmethod
    def get_iface_data(iface, node_id):
        iface_data = {
            'device': node_id,
            'name': iface.name,
            'enabled': iface.enabled,
            'mac_address': iface.mac_address,
            'mtu': iface.effective_mtu
        }

        if iface.vlan and iface.vlan.vid != 0:
            iface_data['mode'] = 200
            vlan = netbox.get_vlan_id_of_site(config.site_name, iface.vlan.vid)
            iface_data['tagged_vlans'] = [vlan]
        else:
            iface_data['mode'] = 100

        if iface.type == enum.InterfaceType.BOND:
            iface_data['form_factor'] = 200
        elif iface.type == enum.InterfaceType.VLAN:
            iface_data['form_factor'] = 32767
        elif iface.type == enum.InterfaceType.BRIDGE:
            iface_data['form_factor'] = 32767

        return iface_data

    @staticmethod
    def get_netbox_node_ifaces_dict(node_id):
        node_ifaces = netbox.get_node_interfaces(node_id)
        ifaces_dict = {}
        for iface in node_ifaces:
            ifaces_dict[iface['name']] = iface['id']
        return ifaces_dict

    def create_interfaces(self, maas_node, netbox_node, netbox_node_ifaces):
        for iface in maas_node.interfaces:
            if iface.type != enum.InterfaceType.UNKNOWN:
                if iface.name not in netbox_node_ifaces:
                    iface_data = self.get_iface_data(iface, netbox_node)
                    netbox_iface_id = netbox.create_interface(iface_data)
                    netbox_node_ifaces[iface.name] = netbox_iface_id

    @staticmethod
    def patch_parent_interfaces(maas_node, netbox_node_ifaces):
        for iface in maas_node.interfaces:
            if iface.type != enum.InterfaceType.UNKNOWN:
                netbox_iface_id = netbox_node_ifaces[iface.name]
                for iface_parent in iface.parents:
                    netbox_parent_iface = netbox_node_ifaces[iface_parent.name]
                    if iface.type == enum.InterfaceType.BOND:
                        netbox.patch_interface(
                            netbox_parent_iface, {'lag': netbox_iface_id})
                    elif iface.type == enum.InterfaceType.VLAN:
                        netbox.patch_interface(
                            netbox_iface_id, {'lag': netbox_parent_iface})
                    elif iface.type == enum.InterfaceType.BRIDGE:
                        netbox.patch_interface(
                            netbox_iface_id, {'lag': netbox_parent_iface})

    @staticmethod
    def create_ip_addresses(maas_node, netbox_node, netbox_node_ifaces):
        for iface in maas_node.interfaces:
            ipv4 = maas.get_interface_ipv4_address(iface)
            if ipv4:
                netbox_iface_id = netbox_node_ifaces[iface.name]
                ip_data = {
                    "address": ipv4,
                    "status": 1,
                    "interface": netbox_iface_id,
                }
                if not netbox.get_ip_address(ipv4):
                    address_id = netbox.create_ip_address(ip_data)
                    if address_id and iface.vlan.vid == 0:
                        netbox.patch_node(
                            netbox_node, {'primary_ip4': address_id})

    @staticmethod
    def update_physical_interfaces(maas_node, netbox_node_ifaces):
        for iface in maas_node.interfaces:
            if iface.type == enum.InterfaceType.PHYSICAL:
                iface_data = {
                    'mtu': iface.effective_mtu
                }
                if iface.vlan and iface.vlan.vid != 0:
                    iface_data['mode'] = 200
                    vlan = netbox.get_vlan_id_of_site(
                        config.site_name, iface.vlan.vid)
                    iface_data['tagged_vlans'] = [vlan]
                else:
                    iface_data['mode'] = 100
                netbox_iface_id = netbox_node_ifaces[iface.name]
                netbox.patch_interface(netbox_iface_id, iface_data)

    @staticmethod
    def get_cable_data(iface, switch_port, color):
        data = {
            "termination_a_type": "dcim.interface",
            "termination_b_type": "dcim.interface",
            "status": True,
            "termination_a_id": iface,
            "termination_b_id": switch_port,
        }
        if color:
            data['color'] = color
        return data

    def create_switch_connections(self, maas_node, netbox_node_ifaces):
        ifaces_details = maas.get_switch_connection_details(maas_node)
        for iface in ifaces_details:
            netbox_iface_id = netbox_node_ifaces[iface['name']]
            netbox_iface = netbox.get_node_interface(netbox_iface_id)
            data = {
                'untagged_vlan': netbox.get_vlan_id_of_site(
                    config.site_name, iface['vid'])
            }
            netbox.patch_interface(netbox_iface_id, data)
            if netbox_iface['lag']:
                netbox.patch_interface(netbox_iface['lag']['id'], data)

            switch = netbox.get_node_by_name(iface['switch_name'])
            switch_port = netbox.get_node_interfaces(
                switch['id'], iface['switch_port'])[0]
            if not netbox.get_cable(netbox_iface_id, switch_port['id']):
                cable_data = self.get_cable_data(
                    netbox_iface_id, switch_port['id'], iface['cable_color'])
                netbox.create_cable(cable_data)

    def create(self):
        for maas_node in self.maas_nodes:
            logging.info(
                'Updating Node: {}...'.format(
                    maas_node.hostname.upper()))

            netbox_node = self.netbox_nodes[maas_node.hostname]
            netbox_node_ifaces = self.get_netbox_node_ifaces_dict(netbox_node)

            # create interface if not present
            self.create_interfaces(maas_node, netbox_node, netbox_node_ifaces)

            # update physical interfaces
            self.update_physical_interfaces(maas_node, netbox_node_ifaces)

            # patch parent interfaces to let them know they are part of this
            # LAG
            self.patch_parent_interfaces(maas_node, netbox_node_ifaces)

            # Create IP addresses of all interfaces of the node
            self.create_ip_addresses(
                maas_node, netbox_node, netbox_node_ifaces)

            # Create Switch Connections
            self.create_switch_connections(maas_node, netbox_node_ifaces)