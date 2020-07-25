import logging
import os
import re
import glob
import platform
import time

from avocado.utils import process

from virttest import virsh
from virttest import utils_misc
from virttest import utils_test
from virttest.libvirt_xml.nodedev_xml import NodedevXML
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.controller import Controller
from virttest.utils_test import libvirt as utlv

def run(test, params, env):
    """
    Sriov basic test:

    1.create max vfs;
    2.Check the nodedev info;
    3.Start a guest with vf;
    4.Reboot a guest with vf;
    5.suspend/resume a guest with vf
    """
    def install_device_driver():
        """
            install device driver
        """
        install_cmd_list =["yum install -y hisi_hpre",
                           "modprobe hisi_hpre"]
        for cmd in install_cmd_list:
            process.run(cmd, shell=True)

    def remove_device_driver():
        """
            remove device driver
        """
        remove_cmd_list = ["rmmod hisi_hpre hisi_qm uacce",
                           "yum remove -y hisi_hpre"]
        for cmd in remove_cmd_list:
            process.run(cmd, shell=True)

    def find_pf():
        pci_address = ""
        if driver == "hisi_hpre":
            pci_address = pci_dirs[0]
            return pci_address
        else:
            return pci_address

    def create_vfs(vf_num):
        """
            Create max vfs.
        """
        net_device = []
        # cleanup env and create vfs
        cmd = "echo 0 > %s/sriov_numvfs" % pci_address
        process.run(cmd, shell=True)
        pci_list = virsh.nodedev_list(cap='pci').stdout.strip().splitlines()
        pci_list_before = set(pci_list)
        cmd = "echo %d > %s/sriov_numvfs" % (vf_num, pci_address)
        test_res = process.run(cmd, shell=True)
        if test_res.exit_status != 0:
            test.fail("Fail to create vfs")

        pci_list_sriov = virsh.nodedev_list(cap='pci').stdout.strip().splitlines()
        pci_list_sriov = set(pci_list_sriov)
        pci_diff = list(pci_list_sriov.difference(pci_list_before))
        for pci_addr in pci_diff:
            temp_addr = pci_addr.split("_")
            pci_addr = ':'.join(temp_addr[1:4]) + '.' + temp_addr[4]
            vf_net_name = os.listdir("%s/%s/uacce" % (pci_device_dir, pci_addr))[0]
            net_device.append(vf_net_name)
        logging.debug(sorted(net_device))

    def check_dev_in():
        """
            Check dev in vm.
        """
        if vm.serial_console is not None:
            vm.cleanup_serial_console()
        vm.create_serial_console()
        session = vm.wait_for_serial_login(timeout=240)

        try:
            output = session.cmd("lspci |grep Huawei", ignore_all_errors=True)
            logging.debug("output is: %s" % output)
            if "HPRE" not in output:
                logging.warning("Not find hpre dev")
        except Exception:
            logging.warning("Not find hpre dev")
        finally:
            session.close()

    def create_nodedev_pci(pci_address):
        """
            Convert xxxx:xx.x to pci_xxxx_xx_xx_x.
        """
        nodedev_addr = pci_address.split(':')[0:2]
        slot_function = pci_address.split(':')[2]
        nodedev_addr.append(slot_function.split('.')[0])
        nodedev_addr.append(slot_function.split('.')[1])
        nodedev_addr.insert(0, "pci")
        nodedev_addr = "_".join(nodedev_addr)
        return nodedev_addr

    def do_operation():
        """
            Do operation in guest os with vf and check the os behavior after operation.
        """
        if operation == "resume_suspend":
            try:
                virsh.suspend(vm.name, debug=True, ignore_status=False)
                virsh.resume(vm.name, debug=True, ignore_statue=False)
                check_dev_in()
                logging.debug("resume_suspend")
            except process.CmdError as detail:
                err_msg = "Suspend-Resume %s with vf failed: %s" % (vm_name, detail)
                test.fail(err_msg)
        if operation == "reboot":
            try:
                if vm.serial_console is not None:
                    vm.cleanup_serial_console()
                    vm.create_serial_console()
                virsh.reboot(vm.name, ignore_status=False)
                check_dev_in()
            except process.CmdError as detail:
                err_msg = "Reboot %s with vf failed: %s" % (vm_name, detail)
                test.fail(err_msg)
        if operation == "save":
            result = virsh.managedsave(vm_name, ignore_status=True, debug=True)
            utils_test.libvirt.check_exit_status(result, expect_error=True)

    def check_info():
        """
            Check the pf or vf info after create vfs.
        """
        if info_type == "pf_info" or info_type == "vf_order":
            nodedev_pci = create_nodedev_pci(pci_address.split("/")[-1])
            xml = NodedevXML.new_from_dumpxml(nodedev_pci)
            if info_type == "pf_info":
                product_info = xml.cap.product_info

                max_count = int(xml.max_count)
                if pci_info.find(product_info) == -1:
                    test.fail("The product_info show in nodedev-dumpxml is wrong\n")
                if max_count != max_vfs:
                    test.fail("The maxCount show in nodedev-dumpxml is wrong\n")
            if info_type == "vf_order":
                vf_addr_list = xml.cap.virt_functions
                if len(vf_addr_list) != max_vfs:
                    test.fail("The num of vf list show in nodedev-dumpxml is wrong\n")
                addr_list = []
                for vf_addr in vf_addr_list:
                    addr = vf_addr.domain+":"+vf_addr.bus+":"+vf_addr.slot+"."+vf_addr.function
                    addr_list.append(addr)
                if sorted(addr_list) != addr_list:
                    test.fail("The vf addr list show in nodedev-dumpxml is not sorted correctly\n")
        elif info_type == "vf_info":
            vf_addr = vf_list[0]
            nodedev_pci = create_nodedev_pci(vf_addr)
            vf_xml = NodedevXML.new_from_dumpxml(nodedev_pci)
            vf_bus_slot = ':'.join(vf_addr.split(':')[1:])
            res = process.run("lspci -s %s -vv" % vf_bus_slot)
            vf_pci_info = res.stdout_text
            vf_product_info = vf_xml.cap.product_info
            if vf_pci_info.find(vf_product_info) == -1:
                test.fail("The product_info show in nodedev-dumpxml is wrong\n")
            pf_addr = vf_xml.cap.virt_functions[0]
            pf_addr_domain = re.findall(r"0x(.+)", pf_addr.domain)[0]
            pf_addr_bus = re.findall(r"0x(.+)", pf_addr.bus)[0]
            pf_addr_slot = re.findall(r"0x(.+)", pf_addr.slot)[0]
            pf_addr_function = re.findall(r"0x(.+)", pf_addr.function)[0]
            pf_pci = pf_addr_domain+":"+pf_addr_bus+":"+pf_addr_slot+"."+pf_addr_function
            if pf_pci != pci_id:
                test.fail("The pf address show in vf nodedev-dumpxml is wrong\n")

    def detach_hostdev():
        """
            Detach hostdev:

            1.Detach hostdev from xml;
            2.Check the live xml after detach hostdev;
            3.Check the vf driver after detach hostdev.
        """
        result = virsh.detach_device(vm_name, new_iface)
        utils_test.libvirt.check_exit_status(result, expect_error=False)
        time.sleep(2)

    def attach_hostdev():
        """
            Attach hostdev:

            1.Attach hostdev from xml;
            2.Check the vf device in vm after attach hostdev;
        """
        if managed == "no":
            result = virsh.nodedev_detach(nodedev_pci_addr)
            utils_test.libvirt.check_exit_status(result, expect_error=False)
        result = virsh.attach_device(vm_name, file_opt=new_iface, flagstr=option, debug=True)
        utils_test.libvirt.check_exit_status(result, expect_error=False)
        if option == "--config":
            result = virsh.start(vm_name)
            utils_test.libvirt.check_exit_status(result, expect_error=False)
        # For option == "--persistent", after VM destroyed and then start, the device should still be there.
        if option == "--persistent":
            virsh.destroy(vm_name)
            result = virsh.start(vm_name, debug=True)
            utils_test.libvirt.check_exit_status(result, expect_error=False)
        check_dev_in()

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(params["main_vm"])
    operation = params.get("operation")
    driver = params.get("driver", "hisi_hpre")
    managed = params.get("managed", "yes")
    attach = params.get("attach", "")
    option = params.get("option", "")
    info_check = params.get("info_check", "no")
    info_type = params.get("info_type", "")
    loop_times = int(params.get("loop_times", "1"))
    start_vm = "yes" == params.get("start_vm", "yes")
    max_vfs_attached = "yes" == params.get("max_vfs_attached", "no")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    vmxml.remove_all_device_by_type('interface')
    vmxml.sync()

    install_device_driver()

    if max_vfs_attached:
        controller_devices = vmxml.get_devices("controller")
        pci_bridge_controllers = []
        for device in controller_devices:
            logging.debug(device)
            if device.type == 'pci' and device.model == "pci-bridge":
                pci_bridge_controllers.append(device)
        if not pci_bridge_controllers and "aarch64" not in platform.platform():
            pci_bridge_controller = Controller("controller")
            pci_bridge_controller.type = "pci"
            pci_bridge_controller.index = "1"
            pci_bridge_controller.model = "pci-bridge"
            vmxml.add_device(pci_bridge_controller)
            vmxml.sync()

    if start_vm:
        if not vm.is_dead():
            vm.destroy()
        vm.start()
        if vm.serial_console is not None:
            vm.cleanup_serial_console()
        vm.create_serial_console()
        session = vm.wait_for_serial_login(timeout=240)
        session.close()
    else:
        if not vm.is_dead():
            vm.destroy()
    driver_dir = "/sys/bus/pci/drivers/%s" % driver
    pci_dirs = glob.glob("%s/000*" % driver_dir)
    pci_device_dir = "/sys/bus/pci/devices"
    pci_address = ""

    # Prepare hostdev xml
    try:
        # pf_iface_name = ""
        pci_address = utils_misc.wait_for(find_pf, timeout=60)
        if not pci_address:
            test.cancel("no up pf found in the test machine")
        pci_id = pci_address.split("/")[-1]
        bus_slot = ':'.join(pci_address.split(':')[1:])
        pci_info = process.run("lspci -s %s -vv" % bus_slot).stdout_text
        max_vfs = int(re.findall(r"Total VFs: (.+?),", pci_info)[0])
        if info_check == 'yes' or max_vfs < 32:
            vf_num = max_vfs
            create_vfs(vf_num)
        else:
            vf_num = int(max_vfs // 2 + 1)
            create_vfs(vf_num)

        vf_list = []
        vf_name_list = []

        for i in range(vf_num):
            vf = os.readlink("%s/virtfn%s" % (pci_address, str(i)))
            vf = os.path.split(vf)[1]
            vf_list.append(vf)
            vf_name = os.listdir('%s/%s/uacce' % (pci_device_dir, vf))[0]
            vf_name_list.append(vf_name)

        if attach == "yes":
            vf_addr = vf_list[0]
            new_iface = utlv.create_hostdev_xml(vf_addr)
            nodedev_pci_addr = create_nodedev_pci(vf_addr)
            origin_driver = os.readlink(os.path.join(pci_device_dir, vf_addr, "driver")).split('/')[-1]
            logging.debug("The driver of vf before attaching to guest is %s\n", origin_driver)
            count = 0
            while count < loop_times:
                logging.debug("count is: %s" % count)
                new_iface = utlv.create_hostdev_xml(vf_addr)
                attach_hostdev()
                if operation != "":
                    do_operation()
                detach_hostdev()
                count += 1

            if max_vfs_attached:
                hostdev_list = []
                for vf_addr in vf_list:
                    new_iface = utlv.create_hostdev_xml(vf_addr)
                    nodedev_pci_addr = create_nodedev_pci(vf_addr)
                    attach_hostdev()
                    hostdev_list.append(new_iface)
                count = 0
                for new_iface in hostdev_list:
                    detach_hostdev()
                    count += 1
        if info_check == "yes":
            check_info()

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
        process.run("echo 0 > %s/sriov_numvfs" % pci_address, shell=True)

        remove_device_driver()
