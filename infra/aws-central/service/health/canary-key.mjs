import { createPrivateKey, createPublicKey, generateKeyPairSync } from "node:crypto";

// The scheduled canary's one install: a P-256 key pair made on the first run and kept in
// SSM Parameter Store as a SecureString (standard tier, which costs nothing; Secrets
// Manager is USD 0.40 a secret a month).
//
// An install id is the hash of the public key, so one kept key is one row in the
// installations table and one "active installation" in the public figures for as long
// as it is kept. A new key on every run, which is what the command-line canary does,
// was a new install every run.
//
// `parameters` is the two calls it needs, shaped like the AWS SDK's (getParameter,
// putParameter). The private key is read, held in memory and signed with. It is never
// logged or returned as text.
export function createStoredIdentity({ parameters, name,
  generate = () => generateKeyPairSync("ec", { namedCurve: "prime256v1" }) }) {
  let held = null;

  async function stored() {
    try {
      return (await parameters.getParameter({ Name: name, WithDecryption: true })).Parameter.Value;
    } catch (error) {
      if (error?.name === "ParameterNotFound") return null;
      throw error;
    }
  }

  async function create() {
    const pem = generate().privateKey.export({ type: "pkcs8", format: "pem" });
    try {
      // Never overwrite: if another run stored a key first, that one is the install.
      await parameters.putParameter({ Name: name, Type: "SecureString", Overwrite: false, Value: pem,
        Description: "Private key of the production health canary's one install. Made by the health function on its first run." });
      return pem;
    } catch (error) {
      if (error?.name !== "ParameterAlreadyExists") throw error;
      return stored();
    }
  }

  return async function identity() {
    if (!held) {
      const privateKey = createPrivateKey((await stored()) || (await create()));
      held = { privateKey, publicKey: createPublicKey(privateKey) };
    }
    return held;
  };
}
