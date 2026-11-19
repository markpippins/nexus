
// This tells TypeScript that CryptoJS is available as a global variable,
// which is true since we're loading it from a CDN in index.html.
declare var CryptoJS: any;

/**
 * Encrypts a plaintext string using AES with a given secret key.
 * @param plainText The string to encrypt.
 * @param secret The secret key for encryption.
 * @returns The Base64-encoded ciphertext.
 */
export const encryptText = (plainText: string, secret: string): string => {
  if (!plainText || !secret) {
    throw new Error("Plaintext and secret key are required for encryption.");
  }
  try {
    const encrypted = CryptoJS.AES.encrypt(plainText, secret);
    return encrypted.toString();
  } catch (error) {
    console.error("Encryption failed:", error);
    throw new Error("Encryption process failed.");
  }
};

/**
 * Decrypts an AES-encrypted ciphertext string with a given secret key.
 * @param cipherText The Base64-encoded ciphertext to decrypt.
 * @param secret The secret key for decryption.
 * @returns The original plaintext string.
 */
export const decryptText = (cipherText: string, secret: string): string => {
  if (!cipherText || !secret) {
    throw new Error("Ciphertext and secret key are required for decryption.");
  }
  try {
    const bytes = CryptoJS.AES.decrypt(cipherText, secret);
    const decryptedText = bytes.toString(CryptoJS.enc.Utf8);
    
    if (!decryptedText) {
      // This often happens if the secret key is incorrect
      throw new Error("Decryption resulted in empty text. Check your secret key.");
    }
    
    return decryptedText;
  } catch (error) {
    console.error("Decryption failed:", error);
    // Provide a more user-friendly error message
    if (error instanceof Error && error.message.includes("Malformed UTF-8 data")) {
        throw new Error("Decryption failed. The secret key is likely incorrect or the ciphertext is corrupt.");
    }
    throw new Error("Decryption process failed. Please verify the ciphertext and secret key.");
  }
};
