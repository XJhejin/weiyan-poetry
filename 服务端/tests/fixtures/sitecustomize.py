"""Test-only vault adapter for protocol tests on machines without Windows DPAPI.

Never bundled into applications. Production Android uses its actual Keystore.
"""
import os
if os.environ.get('WEIYAN_TEST_PORTABLE') == '1' and os.environ.get('WEIYAN_TEST_KEY'):
    import base64
    from nacl.secret import SecretBox
    import secure
    box = SecretBox(base64.b64decode(os.environ['WEIYAN_TEST_KEY']))
    secure.protect = lambda raw, decrypt=False: box.decrypt(raw) if decrypt else bytes(box.encrypt(raw))
