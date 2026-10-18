package com.weiyan.poetry;

import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import java.security.KeyStore;
import java.util.Arrays;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Non-exportable Android Keystore key seals the local vault key and session cache. */
public final class KeyVault {
    private static final String ALIAS = "WeiyanPoetry.local.v1";
    public static synchronized byte[] protect(byte[] input, boolean decrypt) throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (!store.containsAlias(ALIAS)) {
            if (decrypt) throw new IllegalStateException("本机密钥不可用，无法恢复加密记录。");
            KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256).setRandomizedEncryptionRequired(true).build());
            generator.generateKey();
        }
        SecretKey key = (SecretKey) store.getKey(ALIAS, null);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        if (decrypt) {
            if (input.length < 29 || input[0] != 1) throw new IllegalArgumentException("本机加密数据格式无效。");
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(128, Arrays.copyOfRange(input, 1, 13)));
        } else cipher.init(Cipher.ENCRYPT_MODE, key);
        cipher.updateAAD("WeiyanPoetry/local/v1".getBytes(java.nio.charset.StandardCharsets.UTF_8));
        byte[] result = cipher.doFinal(decrypt ? Arrays.copyOfRange(input, 13, input.length) : input);
        if (decrypt) return result;
        byte[] sealed = new byte[13 + result.length];
        sealed[0] = 1;
        System.arraycopy(cipher.getIV(), 0, sealed, 1, 12);
        System.arraycopy(result, 0, sealed, 13, result.length);
        return sealed;
    }
}
