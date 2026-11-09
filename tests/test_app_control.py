import importlib.util
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/backend'))
import proxy_accel as p
spec=importlib.util.spec_from_file_location('control',Path(__file__).resolve().parents[1]/'src/backend/control.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)

class AppTests(unittest.TestCase):
    def test_single_console_acl_has_no_local_bypass(self):
        class Server:
            network=__import__('ipaddress').ip_network('192.168.50.0/24')
            allowed_client='192.168.50.50'
        with patch.object(p,'error'):
            self.assertFalse(p.Server.verify_request(Server(),None,('192.168.50.101',9)))
            self.assertFalse(p.Server.verify_request(Server(),None,('192.168.50.51',9)))
            self.assertTrue(p.Server.verify_request(Server(),None,('192.168.50.50',9)))
    def test_cdn_toggle_changes_order_only(self):
        for optimize,expected in ((True,['23.1.2.4','23.1.2.3']),(False,['23.1.2.3','23.1.2.4'])):
            r=p.Resolver('en0',optimize)
            try:
                with patch.object(p,'doh',return_value=[('23.1.2.3',30),('23.1.2.4',30)]),patch.object(r.pool,'map',return_value=[(50,'23.1.2.3'),(10,'23.1.2.4')]):
                    self.assertEqual(r.candidates('ctest.cdn.nintendo.net',443),expected)
            finally:r.pool.shutdown()
    def test_startup_option_and_no_power_program(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            app=base/'Test.app';(app/'Contents/MacOS').mkdir(parents=True);(app/'Contents/MacOS/NS2Direct').touch()
            with patch.dict('os.environ',{'NS2_APP_PATH':str(app)}),patch.object(c,'BASE',base),patch.object(c,'RUNTIME',base/'service.plist'),patch.object(c,'AGENT',base/'agents/service.plist'):
                c.write_agents({'launch_at_login':True})
                self.assertFalse(c.AGENT.exists())
                app_agent=c.AGENT.with_name(c.APP_LABEL+'.plist')
                login=plistlib.loads(app_agent.read_bytes())
                self.assertEqual(login['ProgramArguments'][:2],['/usr/bin/open','-g'])
                self.assertNotIn('KeepAlive',login)
                args=plistlib.loads(c.RUNTIME.read_bytes())['ProgramArguments']
                self.assertEqual(args[0],'/opt/homebrew/bin/python3')
                self.assertFalse(any('caffeinate' in arg for arg in args))
                c.write_agents({'launch_at_login':False})
                self.assertFalse(app_agent.exists());self.assertTrue(c.RUNTIME.exists())
    def test_traffic_is_game_data_only_and_independent_of_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);(base/'runtime').mkdir()
            path=base/'runtime/traffic.json'
            path.write_text(json.dumps({'worker_pid':42,'time':100,'clients':{
                '192.168.50.2':{'total_bytes':123456,'active_downloads':2},
                '192.168.50.3':{'total_bytes':654321,'active_downloads':0}}}))
            with patch.object(c,'BASE',base):
                result=c.traffic_status(True,42,now=102)
                self.assertTrue(result['available'])
                self.assertEqual(result['total_bytes'],777777)
                self.assertEqual(result['active_downloads'],2)
                self.assertFalse(c.traffic_status(True,99,now=102)['available'])
                self.assertFalse(c.traffic_status(True,42,now=121)['available'])
                self.assertFalse(c.traffic_status(False,42,now=102)['available'])
                path.write_text('{bad')
                self.assertFalse(c.traffic_status(True,42,now=102)['available'])

    def test_saving_recovery_parameters_does_not_stop_running_service(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);config=base/'config.json'
            original={'interface':'en0','port':8888,'allowed_subnet':'192.168.50.0/24','allowed_client':'','optimize_cdn':True,'launch_at_login':True,'auto_recovery':True}
            config.write_text(json.dumps(original))
            with patch.object(c,'BASE',base),patch.object(c,'CONFIG',config),patch.object(c,'interfaces',return_value=[{'device':'en0'}]),patch.object(c,'process_state',return_value=(True,123)),patch.object(c,'write_agents'),patch.object(c,'status',return_value={'ok':'saved'}),patch.object(c,'stop') as stop,patch.object(c,'start') as start:
                result=c.save({'recovery_threshold_kbps':125,'recovery_low_seconds':75,'recovery_pause_seconds':18,'recovery_wait_seconds':190})
                self.assertEqual(result,{'ok':'saved'})
                stop.assert_not_called();start.assert_not_called()
                data=json.loads(config.read_text())
                self.assertEqual([data[k] for k in c.RECOVERY_DEFAULTS],[125,75,18,190])
    def test_configuration_cannot_select_vpn_or_external_client(self):
        config={'port':8888,'interface':'en0','allowed_subnet':'192.168.50.0/24','allowed_client':'','optimize_cdn':True,'launch_at_login':False}
        with patch.object(c,'interfaces',return_value=[{'device':'en0'}]):
            for updates in ({'interface':'utun6'},{'allowed_client':'10.0.0.1'},{'allowed_subnet':'0.0.0.0/0'},{'port':53}):
                with self.assertRaises(ValueError):c.validate(dict(config,**updates))
            self.assertEqual(c.validate(dict(config,allowed_client='192.168.50.50'))['allowed_client'],'192.168.50.50')

if __name__=='__main__':unittest.main(verbosity=2)
