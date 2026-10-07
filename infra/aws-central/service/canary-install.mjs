// Which install is the scheduled health canary's.
//
// The public figures count people, and the canary is not one: for its install the
// service writes no metric and marks the request line `canary: true` (core.mjs). The
// health function makes its key on its first run, so the id is not known when the stack
// is deployed. It publishes the id (an install id is the hash of a public key, and no
// secret) as a plain SSM parameter, and `read` fetches that one parameter.
//
// Never read inside a request: the start-up warm and the warm event that arrives every
// minute call refresh(), which asks at most once in ten minutes, and a request reads the
// value held in memory. A read that fails keeps the last id and is logged. Until the
// first read succeeds nobody is the canary, which only means its requests are counted.
export function createCanaryInstall({ read, ttlMs = 600_000, now = Date.now, logger = console }) {
  let held = null;
  let readAt = null;
  return {
    id: () => held,
    async refresh() {
      if (readAt !== null && now() - readAt < ttlMs) return;
      readAt = now();
      try {
        const value = String((await read()) || "").trim();
        held = /^[a-f0-9]{32}$/.test(value) ? value : null;
      } catch (error) {
        logger.error(JSON.stringify({ event: "canary_install_unreadable", error_type: error?.name || "Error",
          error_message: String(error?.message || error).slice(0, 300) }));
      }
    },
  };
}

// `read` for the deployed function: one GetParameter on the parameter the health
// function publishes. The central package carries no SSM client; the nodejs22.x runtime
// provides one, and it is loaded here on first use, not at the top of handler.mjs, so a
// client that cannot be loaded costs the exclusion and never the service.
export function parameterReader({ name, sdk = () => import("@aws-sdk/client-ssm") }) {
  let client = null;
  return async function read() {
    if (!name) return null;
    client ||= await sdk().then(({ GetParameterCommand, SSMClient }) => ({ ssm: new SSMClient({}), GetParameterCommand }));
    try {
      return (await client.ssm.send(new client.GetParameterCommand({ Name: name }))).Parameter?.Value || null;
    } catch (error) {
      // The health function has not run its first full canary yet.
      if (error?.name === "ParameterNotFound") return null;
      throw error;
    }
  };
}
