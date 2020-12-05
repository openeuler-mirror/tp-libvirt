"""
vmtop
"""
import os
import logging

from avocado.utils import process
from avocado.utils import path
from avocado.core import exceptions

import virttest.utils_libguestfs as lgf
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest import virsh
from virttest import data_dir

def run(test, params, env):
    """
    Test for vmtop, it is a top like tool for vm

    1. Install vmtop
    2. Test vmtop life cycle: judge if vmtop running successfully
        as expected by Domains's number
    3. Test vm other action : vcpuhotplug, multivmtop, cmdline, data_events
    """
    # pylint: disable=R0914, C0103, R0912, R0915
    def vmtop_info(vmtop, output_path, info_type):
        """
        Collect and return vmtop infomation
        """
        cmd = "%s -b -H -n 1 > %s" % (vmtop, output_path)
        process.run(cmd, ignore_status=True, shell=True)

        if info_type == "Domains":
            cmd = "cat %s | grep  Domains | awk '{ print $2 }'" % output_path
            res = process.run(cmd, ignore_status=True, shell=True)
        elif info_type == "Cpu":
            cmd = "cat %s | grep /KVM | awk 'END{ print NR }'" % output_path
            res = process.run(cmd, ignore_status=True, shell=True)
        elif info_type == "Data":
            cmd = "cat %s | grep  DID" % output_path
            res = process.run(cmd, ignore_status=True, shell=True)

        return res.stdout_text

    def vmtop_interval(vmtop, interval, output_path):
        """
        Collect and return vmtop's interval time
        """
        cmd = "%s -b -d %s -n 2 > %s" % (vmtop, interval, output_path)
        process.run(cmd, ignore_status=True, shell=True)
        cmd = "cat %s | grep  vmtop | awk -F[-] '{ print $4 }' | awk -F[:] '{ print $3 }'" \
              % output_path
        res = process.run(cmd, ignore_status=True, shell=True)

        return res.stdout_text

    vmname = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vmname)

    output = params.get("output_file", "output")
    output_path = os.path.join(data_dir.get_tmp_dir(), output)
    option = params.get("option")
    interval = params.get("interval")
    test_type = params.get("test_type", "")
    vcpu_placement = params.get("vcpu_placement")
    vcpu_cur = int(params.get("vcpu_cur"))
    vcpu_max = int(params.get("vcpu_max"))
    vcpu_live = int(params.get("vcpu_live"))
    vm_num = int(params.get("vm_num"))
    vmtopnum = params.get("vmtopnum")

    # Backup domain XML
    vmxml = vm_xml.VMXML.new_from_dumpxml(vmname)

    std_events = ['DID', 'VM/task-name', 'PID', '%CPU', 'EXThvc', 'EXTwfe', 'EXTwfi',\
                  'EXTmmioU', 'EXTmmioK', 'EXTfp', 'EXTirq', 'EXTsys64', 'EXTmabt',\
                  'EXTsum', 'S', 'P', '%ST', '%GUE', '%HYP']

    try:
        if test_type == "multi_vms":
            vm_clone_name = []
            num = 1
            while num < vm_num:
                vm_clone_name.append("%s_%s" % (vmname, num))
                lgf.virt_clone_cmd(vmname, vm_clone_name[num - 1], True, timeout=360)
                virsh.start(vm_clone_name[num - 1], ignore_status=True)
                num = num + 1

        if vcpu_placement:
            # Set vcpu: placement,current,max vcpu
            vmxml.placement = vcpu_placement
            vmxml.vcpu = vcpu_max
            vmxml.current_vcpu = vcpu_cur
            del vmxml.cpuset
            vmxml.sync()
            logging.info("Start VM with vcpu hotpluggable")

        virsh.start(vmname, ignore_status=True)

        #Install vmtop command
        install_cmd = "yum install -y vmtop"
        process.run(install_cmd, shell=True)
        #Find vmtop command
        try:
            vmtop = path.find_command("vmtop")
        except path.CmdNotFoundError as info:
            raise exceptions.TestSkipError("No vmtop command found - %s" % info)

        #Test vmtop when start vm
        res = vmtop_info(vmtop, output_path, info_type="Domains")
        if int(res) != vm_num:
            test.fail("start failed and the res = %s" % res)

        if test_type == "data_events":
            res = vmtop_info(vmtop, output_path, info_type="Data")
            events = res.split()
            order = 0
            for event in events:
                if event != std_events[order]:
                    test.fail("error event:%s" % event)
                order = order + 1

        #Test vmtop when other vm action happened
        if test_type == "destroy":
            vm.destroy()
            res = vmtop_info(vmtop, output_path, info_type="Domains")

            if int(res) == (vm_num - 1):
                logging.info("Test destroy successfully!")
            else:
                test.fail("shutdown failed and the res = %s" % res)
        elif test_type == "reboot":
            vm.reboot()
            res = vmtop_info(vmtop, output_path, info_type="Domains")
            if int(res) != vm_num:
                test.fail("reboot failed and the res = %s" % res)
        elif test_type == "pause":
            vm.pause()
            res = vmtop_info(vmtop, output_path, info_type="Domains")
            if int(res) != vm_num:
                test.fail("pause failed and the res = %s" % res)
            vm.resume()
            res = vmtop_info(vmtop, output_path, info_type="Domains")
            if int(res) != vm_num:
                test.fail("resume failed and the res = %s" % res)

        # Test vmtop with invalid cmdline
        if option == "d":
            res = vmtop_interval(vmtop, interval, output_path)
            output = res.splitlines()
            fir_time = output[0].split()
            sec_time = output[1].split()
            if (int(sec_time[0]) - int(fir_time[0])) == int(interval) \
                    or (int(sec_time[0]) + 60 - int(fir_time[0])) == int(interval):
                logging.info("Test option -d successfully")
            else:
                test.fail("Interval error!")

        #Test multivmtop
        if vmtopnum:
            cmd = " nohup vmtop -n 10 2>&1 &"
            num = 0
            while num < int(vmtopnum):
                process.run(cmd, ignore_status=True, shell=True)
                num = num + 1

            res = vmtop_info(vmtop, output_path, info_type="Domains")
            if int(res) != vm_num:
                test.fail("multivntop start failed and the res = %s" % res)

        #Test vcpu_hotpulggable
        if test_type == "vcpu_hotplug":
            virsh.setvcpus(vmname, vcpu_live, ignore_status=False, debug=True)
            res = vmtop_info(vmtop, output, info_type="Cpu")

            if int(res) == vcpu_live:
                logging.info("Test vcpu_hotplug successfully!")
            else:
                test.fail("Error result:%s" % res)

        #Test vmtop with invalid cmdline
        if option == "x":
            cmd = "%s -%s" % (vmtop, option)
            res = process.run(cmd, ignore_status=True, shell=True)
            libvirt.check_exit_status(res, expect_error=True)


    finally:
        # Destroy vm_clone
        if test_type == "multi_vms":
            num = 1
            while num < vm_num:
                if virsh.domain_exists(vm_clone_name[num - 1]):
                    if virsh.is_alive(vm_clone_name[num - 1]):
                        virsh.destroy(vm_clone_name[num - 1])
                        virsh.remove_domain(vm_clone_name[num - 1], \
                                            "--remove-all-storage", debug=True)
                num = num + 1

        if vm.is_alive():
            vm.destroy()
        vmxml.sync()
