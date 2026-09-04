export default {
  async scheduled(controller, env, ctx) {
    const shift = getShiftFromIST(new Date());

    console.log(`Cloudflare cron fired for ${shift} shift`);
    await dispatchGitHubWorkflow(env, { shift });
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    const isCheckRoute = url.pathname === "/check" || url.pathname === "/";

    if (request.method === "POST" && isCheckRoute) {
      try {
        const payload = await request.json();
        const text = (payload?.message?.text || "").trim();
        const command = parseCheckCommand(text);

        if (command) {
          await dispatchGitHubWorkflow(env, command);
          const dateLabel = command.date ? ` for ${command.date}` : "";
          return new Response(`Triggered GitHub workflow for ${command.shift} shift${dateLabel}`, { status: 200 });
        }

        return new Response("No valid shift requested. Use /check A or /check A 03/09/2026", { status: 400 });
      } catch (error) {
        console.error("/check request failed:", error);
        return new Response("Bad request", { status: 400 });
      }
    }

    return new Response("Worker is running", { status: 200 });
  }
};

function parseCheckCommand(text) {
  if (!text) return null;

  const normalized = text.trim();
  const patterns = [
    /^\/\s*(?:check|run)\s+([ABC])(?:\s+(\d{2}\/\d{2}\/\d{4}))?$/i,
    /^([ABC])(?:\s+(\d{2}\/\d{2}\/\d{4}))?$/i
  ];

  for (const pattern of patterns) {
    const match = normalized.match(pattern);
    if (match) {
      return {
        shift: match[1].toUpperCase(),
        date: match[2] || undefined
      };
    }
  }

  return null;
}

function getShiftFromIST(date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).formatToParts(date);

  const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const hour = Number(map.hour || 0);

  if (5 <= hour && hour < 13) return "C";
  if (13 <= hour && hour < 21) return "A";
  return "B";
}

async function dispatchGitHubWorkflow(env, { shift, date }) {
  const repo = env.GH_REPO || "sks7205/checklist_tracker";
  const token = env.GH_PAT;

  if (!token) {
    throw new Error("GH_PAT is not configured in Cloudflare worker secrets");
  }

  const payload = {
    ref: "main",
    inputs: { shift }
  };

  if (date) {
    payload.inputs.date = date;
  }

  const response = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/shift_check.yml/dispatches`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${token}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`GitHub dispatch failed: ${response.status} ${text}`);
  }

  const logDate = date ? ` on ${date}` : "";
  console.log(`Triggered workflow for ${shift} shift${logDate}`);
}
