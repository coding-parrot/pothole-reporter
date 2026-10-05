"""Execute the actual patched native-injected script before and after DOM creation."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SafeAreaRace(unittest.TestCase):
    def test_document_root_can_be_absent_during_native_startup(self):
        source = (ROOT / 'android-app/node_modules/@capacitor/android/capacitor/src/main/java/com/getcapacitor/plugin/SystemBars.java').read_text()
        start = source.index('String script = String.format(', source.index('private void injectSafeAreaCSS'))
        script = source[start:].split('"""', 2)[1].replace('%d', '12')
        runner = r'''
const vm = require('node:vm'), assert = require('node:assert/strict');
const code = require('node:fs').readFileSync(0,'utf8');
let listener, styles = {}, errors = [];
const document = {documentElement:null, addEventListener(name, fn, options) {
  assert.equal(name,'DOMContentLoaded'); assert.equal(options.once,true); listener=fn;
}};
vm.runInNewContext(code, {document,console:{error:(...args)=>errors.push(args)}});
assert.equal(errors.length,0); assert.equal(typeof listener,'function');
document.documentElement={style:{setProperty:(name,value)=>styles[name]=value}};
listener(); assert.equal(Object.keys(styles).length,4);
listener=null; styles={};
vm.runInNewContext(code, {document,console:{error:(...args)=>errors.push(args)}});
assert.equal(listener,null); assert.equal(Object.keys(styles).length,4); assert.equal(errors.length,0);
'''
        subprocess.run(['node', '-e', runner], input=script, text=True, check=True)


if __name__ == '__main__':
    unittest.main()
