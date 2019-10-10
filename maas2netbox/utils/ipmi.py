import subprocess


def get_mac_address(ipv4, username, password):
    cmd = [
        "/bin/bash",
        "-c",
        "ipmitool -I lanplus -H {} -U {} -P {} lan print |"
        " grep 'MAC Address' | awk '{{print $4}}'"
        .format(ipv4, username, password)]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)
    try:
        outs, errs = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        outs, errs = proc.communicate()

    return outs[:-1]


def get_firmware_versions(ipv4, username, password):
    cmd = [
        "/bin/bash",
        "-c",
        "/opt/lenovo/osput"
        "-c getServerInfo -u {} -p {} -H {}"
        .format(username, password, ipv4)]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)
    try:
        outs, errs = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        outs, errs = proc.communicate()

    return outs[:-1]


def parse_firmware_versions(output):
    firmware_versions = {}
    keeping_lines = [
        line.split(': ')[1] for line in output.decode().split('\n')
        if (
            line.startswith('Device type:')
            or line.startswith('Current version:'))]
    it = iter(keeping_lines)
    for x in it:
        version = next(it)
        if x == 'BIOS':
            firmware_versions[x] = version
        elif x == 'PSU':
            if x in firmware_versions and \
                    firmware_versions[x] != 'Not available':
                continue
            firmware_versions[x] = version
        elif x == 'System Manager':
            firmware_versions['TSM'] = version

    return firmware_versions
