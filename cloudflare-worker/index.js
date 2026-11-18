const jsonHeaders = { "content-type": "application/json" };
let commandMenuSet = false;

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: jsonHeaders });
}

function base64(bytes) {
  let binary = "";
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode(...bytes.slice(index, index + 0x8000));
  }
  return btoa(binary);
}

function decodeContent(content) {
  const binary = atob(content.replace(/\n/g, ""));
  return new TextDecoder().decode(Uint8Array.from(binary, char => char.charCodeAt(0)));
}

function encodeContent(content) {
  return base64(new TextEncoder().encode(content));
}

async function github(request, env, path, options = {}) {
  const response = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/${path}`, {
    ...options,
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "user-agent": "autovid-telegram-worker",
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    throw new Error(`GitHub API ${response.status}: ${(await response.text()).slice(0, 300)}`);
  }
  return response.status === 204 ? null : response.json();
}

async function readFile(env, path) {
  try {
    const file = await github(null, env, `contents/${path}?ref=${encodeURIComponent(env.GITHUB_BRANCH || "main")}`);
    return { text: decodeContent(file.content), sha: file.sha };
  } catch (error) {
    if (error.message.startsWith("GitHub API 404")) return { text: "", sha: null };
    throw error;
  }
}

async function writeFile(env, path, text, sha, message) {
  await github(null, env, `contents/${path}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      message,
      content: encodeContent(text),
      branch: env.GITHUB_BRANCH || "main",
      ...(sha ? { sha } : {}),
    }),
  });
}

async function sendTelegram(env, chatId, text) {
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
  if (!response.ok) throw new Error(`Telegram API ${response.status}`);
}

async function setTelegramCommands(env) {
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/setMyCommands`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      commands: [
        { command: "create", description: "Create a video for review" },
        { command: "remaining", description: "Show remaining topics" },
        { command: "add", description: "Add a topic to the queue" },
        { command: "upload", description: "Upload the replied video" },
        { command: "help", description: "Show bot instructions" },
      ],
    }),
  });
  const result = await response.json();
  if (!response.ok || !result.ok) {
    throw new Error(`Telegram setMyCommands failed: ${JSON.stringify(result).slice(0, 300)}`);
  }
  commandMenuSet = true;
}

function safeErrorMessage(error, env) {
  let message = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  for (const secret of [env.GITHUB_TOKEN, env.TELEGRAM_BOT_TOKEN, env.TELEGRAM_WEBHOOK_SECRET]) {
    if (secret) message = message.split(secret).join("[redacted]");
  }
  if (message.includes("GitHub API 403") && message.includes("Resource not accessible by personal access token")) {
    return "GitHub denied the request (403). Update the Cloudflare Worker secret GITHUB_TOKEN with a fine-grained token that has access to smsmorsalin/autovid and Contents: Read and write permission.";
  }
  return message.replace(/\s+/g, " ").slice(0, 700);
}

async function queueCount(env) {
  const [scripts, topics, done] = await Promise.all([
    readFile(env, "scripts.json"),
    readFile(env, "topics.txt"),
    readFile(env, "state/done.json"),
  ]);
  const completed = new Set((done.text ? JSON.parse(done.text) : []).map(item => item.topic));
  const queued = scripts.text ? JSON.parse(scripts.text).filter(item => !item.done).length : 0;
  const topicsCount = topics.text.split(/\r?\n/).filter(line => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) return false;
    const parts = trimmed.split("|");
    const topic = ["explainer", "timeline3d"].includes(parts[0].trim())
      ? parts.slice(1).join("|").trim() : trimmed;
    return topic && !completed.has(topic);
  }).length;
  return queued + topicsCount;
}

async function dispatchUpload(env, update) {
  await github(null, env, "dispatches", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      event_type: "telegram_upload",
      client_payload: { update },
    }),
  });
}

async function dispatchCreate(env, topic, style, updateId) {
  const workflow = await readFile(env, ".github/workflows/telegram-create.yml");
  if (!workflow.sha) {
    throw new Error("Telegram create workflow is not on GitHub yet. Push the project changes to the main branch first.");
  }
  await github(null, env, "dispatches", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      event_type: "telegram_create_video",
      client_payload: { topic, style, update_id: updateId },
    }),
  });
}

async function processUpdate(env, update) {
  const message = update.message;
  if (!message) return;
  if (!commandMenuSet) {
    try {
      await setTelegramCommands(env);
    } catch (error) {
      console.error("Could not register Telegram command menu", safeErrorMessage(error, env));
    }
  }
  const chatId = String(message.chat?.id || "");
  const raw = String(message.text || "").trim();
  const [first, ...rest] = raw.split(/\s+/);
  const command = first.toLowerCase().split("@", 1)[0];
  const argument = rest.join(" ").trim();

  if (["/start", "start", "/help", "help"].includes(command)) {
    await sendTelegram(env, chatId, "ভিডিও তৈরি: create <topic> লিখুন। ভিডিও রিভিউ পাওয়ার পর সেই মেসেজের reply হিসেবে upload লিখলে প্রকাশ হবে।\n/remaining - বাকি টপিক\n/add <topic> - কিউতে টপিক যোগ\n/help - সাহায্য");
  } else if (["/remaining", "/remaning", "remaining", "remaning", "/queue", "queue"].includes(command)) {
    await sendTelegram(env, chatId, `বাকি স্ক্রিপ্ট: ${await queueCount(env)}`);
  } else if (["/create", "create"].includes(command)) {
    let topic = argument;
    if ((topic.startsWith('"') && topic.endsWith('"')) ||
        (topic.startsWith("'") && topic.endsWith("'")) ||
        (topic.startsWith("“") && topic.endsWith("”"))) {
      topic = topic.slice(1, -1).trim();
    }
    let style = "explainer";
    const separator = topic.indexOf("|");
    if (separator >= 0 && ["explainer", "timeline3d"].includes(topic.slice(0, separator).trim())) {
      style = topic.slice(0, separator).trim();
      topic = topic.slice(separator + 1).trim();
    }
    if (!topic) {
      await sendTelegram(env, chatId, 'ব্যবহার: create "আপনার ভিডিওর টপিক"');
    } else if (topic.length > 500) {
      await sendTelegram(env, chatId, "টপিক 500 অক্ষরের বেশি হতে পারবে না।");
    } else {
      await dispatchCreate(env, topic, style, update.update_id);
      await sendTelegram(env, chatId, `🎬 ভিডিও তৈরির কাজ শুরু হয়েছে: ${topic}\nতৈরি হলে রিভিউ ভিডিও পাঠাব।`);
    }
  } else if (["/add", "add"].includes(command)) {
    if (!argument) {
      await sendTelegram(env, chatId, "ব্যবহার: /add <বাংলা স্ক্রিপ্ট বা টপিক>");
    } else if (argument.length > 500) {
      await sendTelegram(env, chatId, "স্ক্রিপ্ট 500 অক্ষরের বেশি হতে পারবে না।");
    } else {
      const file = await readFile(env, "scripts.json");
      const queue = file.text ? JSON.parse(file.text) : [];
      const parts = argument.split("|");
      const hasStyle = ["explainer", "timeline3d"].includes(parts[0].trim());
      const style = hasStyle ? parts[0].trim() : "explainer";
      const topic = hasStyle ? parts.slice(1).join("|").trim() : argument;
      queue.push({ topic, style, done: false });
      await writeFile(env, "scripts.json", JSON.stringify(queue, null, 2) + "\n", file.sha, "autovid: add Telegram script");
      await sendTelegram(env, chatId, `স্ক্রিপ্ট তালিকায় যোগ হয়েছে। বাকি: ${await queueCount(env)}`);
    }
  } else if (["upload", "/upload"].includes(command)) {
    const reply = message.reply_to_message;
    const state = await readFile(env, "state/telegram.json");
    const pending = state.text ? (JSON.parse(state.text).pending || {}) : {};
    const replyId = String(reply?.message_id || "");
    const replyHasVideo = Boolean(
      reply?.video || String(reply?.document?.mime_type || "").startsWith("video/")
    );
    const matchingPending = replyId ? pending[replyId] : null;
    const pendingWithVideo = Object.entries(pending).filter(([, item]) => item?.video_file_id);

    if (reply) {
      if (!replyHasVideo && !matchingPending?.video_file_id) {
        await sendTelegram(env, chatId, "এই মেসেজের সঙ্গে কোনো আপলোডযোগ্য ভিডিও নেই। ভিডিও রিভিউ মেসেজের reply হিসেবে upload লিখুন।");
        return;
      }
    } else if (pendingWithVideo.length === 0) {
      await sendTelegram(env, chatId, "এখন আপলোড করার কোনো ভিডিও নেই। আগে একটি ভিডিও তৈরি করুন বা ভিডিও রিভিউ মেসেজের reply হিসেবে upload লিখুন।");
      return;
    } else if (pendingWithVideo.length > 1) {
      await sendTelegram(env, chatId, "একাধিক ভিডিও অপেক্ষা করছে। যে ভিডিওটি চান তার রিভিউ মেসেজের reply হিসেবে upload লিখুন।");
      return;
    }
    await dispatchUpload(env, update);
    await sendTelegram(env, chatId, "আপলোড প্রসেসিং শুরু হয়েছে। শেষ হলে ফলাফল জানানো হবে।");
  }
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return json({ ok: true, service: "autovid telegram webhook" });
    if (env.TELEGRAM_WEBHOOK_SECRET &&
        request.headers.get("x-telegram-bot-api-secret-token") !== env.TELEGRAM_WEBHOOK_SECRET) {
      return json({ ok: false }, 401);
    }

    let update;
    try {
      update = await request.json();
    } catch (error) {
      console.error("Invalid Telegram update JSON", error);
      return json({ ok: false }, 400);
    }

    const message = update.message;
    if (!message) return json({ ok: true });
    const chatId = String(message.chat?.id || "");
    if (env.TELEGRAM_CHAT_ID && chatId !== String(env.TELEGRAM_CHAT_ID)) return json({ ok: true });

    // Acknowledge Telegram before making GitHub or Bot API calls. Telegram retries
    // webhook updates when it receives a non-2xx response or waits too long; those
    // retries used to resend the "upload started" message and dispatch the job again.
    ctx.waitUntil((async () => {
      try {
        await processUpdate(env, update);
      } catch (error) {
        console.error("Telegram update processing failed", {
          updateId: update.update_id,
          error: safeErrorMessage(error, env),
        });
        try {
          await sendTelegram(
            env,
            chatId,
            `⚠️ কমান্ডটি সম্পন্ন করা যায়নি।\nকারণ: ${safeErrorMessage(error, env)}\nGitHub Actions run: https://github.com/${env.GITHUB_REPO}/actions`
          );
        } catch (replyError) {
          console.error("Could not send Telegram failure reply", String(replyError));
        }
      }
    })());
    return json({ ok: true });
  },
};
