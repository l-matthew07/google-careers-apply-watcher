/**
 * AWS Lambda: Google Careers apply-button watcher with Photon Spectrum
 * iMessage alerts.
 *
 * Runs on an EventBridge schedule. Checks every URL in JOB_URLS; when a
 * posting's Apply button goes live (or a posting disappears), DMs every
 * number in RECIPIENTS over iMessage via Spectrum Cloud. Already-notified
 * URLs are remembered in an SSM parameter so you get exactly one message
 * per event.
 *
 * Environment variables:
 *     JOB_URLS        comma- or newline-separated job posting URLs
 *     PROJECT_ID      Spectrum Cloud project (Photon dashboard → Settings)
 *     PROJECT_SECRET  its API secret
 *     RECIPIENTS      comma-separated E.164 numbers; each must be a
 *                     registered project user on the Photon dashboard
 *     EMAIL_FROM      optional; SES-verified sender for email alerts
 *     EMAIL_TO        optional; comma-separated email alert recipients
 *     STATE_PARAM     SSM parameter name (default /apply-watcher/state)
 */

import { Spectrum } from "spectrum-ts";
import { imessage } from "@spectrum-ts/imessage";
import {
  SSMClient,
  GetParameterCommand,
  PutParameterCommand,
} from "@aws-sdk/client-ssm";
import { SESv2Client, SendEmailCommand } from "@aws-sdk/client-sesv2";

const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";
const APPLY_RE = /href="(\.\/apply\?jobId=[^"]+)"/;

const ssm = new SSMClient({});
const ses = new SESv2Client({});

type Status = "live" | "waiting" | "gone" | "error";
type State = { notified: string[] };

function jobName(url: string): string {
  const m = url.match(/\/jobs\/results\/\d+-([a-z0-9-]+)/);
  return m?.[1] ? m[1].replace(/-/g, " ") : url.slice(0, 60);
}

function decodeEntities(s: string): string {
  return s.replace(/&amp;/g, "&").replace(/&#39;/g, "'").replace(/&quot;/g, '"');
}

async function check(jobUrl: string): Promise<[Status, string | null]> {
  let resp: Response;
  try {
    resp = await fetch(jobUrl, {
      headers: { "User-Agent": UA },
      signal: AbortSignal.timeout(25_000),
    });
  } catch {
    return ["error", null];
  }
  if (resp.status === 404) return ["gone", null];
  if (!resp.ok) return ["error", null];

  const page = await resp.text();
  const m = page.match(APPLY_RE);
  if (m?.[1]) {
    const base = (jobUrl.split("?")[0] ?? jobUrl).replace(/\/+$/, "").replace(/\/[^/]*$/, "");
    return ["live", `${base}/${decodeEntities(m[1].slice(2))}`];
  }
  const title = page.match(/<title>([^<]*)<\/title>/);
  if (!title?.[1] || title[1].trim().toLowerCase().startsWith("jobs search")) {
    return ["gone", null];
  }
  return ["waiting", null];
}

async function loadState(param: string): Promise<State> {
  try {
    const out = await ssm.send(new GetParameterCommand({ Name: param }));
    return JSON.parse(out.Parameter?.Value ?? "");
  } catch {
    return { notified: [] };
  }
}

async function saveState(param: string, state: State): Promise<void> {
  await ssm.send(new PutParameterCommand({
    Name: param,
    Value: JSON.stringify(state),
    Type: "String",
    Overwrite: true,
  }));
}

async function emailAlerts(alerts: string[]): Promise<void> {
  const from = process.env.EMAIL_FROM;
  const to = (process.env.EMAIL_TO ?? "")
    .split(",").map(s => s.trim()).filter(Boolean);
  if (!from || to.length === 0) return;
  try {
    await ses.send(new SendEmailCommand({
      FromEmailAddress: from,
      Destination: { ToAddresses: to },
      Content: {
        Simple: {
          Subject: { Data: "Google Careers apply-watcher alert" },
          Body: { Text: { Data: alerts.join("\n\n") } },
        },
      },
    }));
    console.log(`email -> ${to.join(", ")}: sent`);
  } catch (e) {
    // Likely SES sandbox (recipient unverified) or identity not yet
    // verified. Don't block the iMessage path.
    console.error(`email -> ${to.join(", ")}: FAILED — ${e}`);
  }
}

async function sendAlerts(alerts: string[]): Promise<void> {
  const recipients = (process.env.RECIPIENTS ?? "")
    .split(",").map(s => s.trim()).filter(Boolean);
  const app = await Spectrum({
    projectId: process.env.PROJECT_ID!,
    projectSecret: process.env.PROJECT_SECRET!,
    providers: [imessage.config()],
  });
  try {
    const im = imessage(app);
    for (const phone of recipients) {
      for (const text of alerts) {
        try {
          const dm = await im.space.create(await im.user(phone));
          await dm.send(text);
          console.log(`iMessage -> ${phone}: sent`);
        } catch (e) {
          // Likely "Target not allowed": number not registered as a project
          // user on the Photon dashboard. Don't block the other sends.
          console.error(`iMessage -> ${phone}: FAILED — ${e}`);
        }
      }
    }
  } finally {
    await app.stop();
  }
}

export const handler = async (): Promise<Record<string, Status>> => {
  const param = process.env.STATE_PARAM ?? "/apply-watcher/state";
  const urls = (process.env.JOB_URLS ?? "")
    .split(/[,\n]/).map(s => s.trim()).filter(Boolean);
  const state = await loadState(param);
  const results: Record<string, Status> = {};
  const alerts: string[] = [];

  for (const url of urls) {
    const [status, applyUrl] = await check(url);
    results[jobName(url)] = status;
    const keyLive = `live:${url}`;
    const keyGone = `gone:${url}`;

    if (status === "live" && !state.notified.includes(keyLive)) {
      alerts.push(`🚨 Google Apply button is LIVE: ${jobName(url)}\nApply now: ${applyUrl ?? url}`);
      state.notified.push(keyLive);
    } else if (status === "gone" && !state.notified.includes(keyGone)) {
      alerts.push(`Google posting no longer resolves (taken down?): ${jobName(url)}\n${url}`);
      state.notified.push(keyGone);
    }
  }

  if (alerts.length > 0) {
    await emailAlerts(alerts);
    await sendAlerts(alerts);
  }
  await saveState(param, state);
  console.log(JSON.stringify(results));
  return results;
};
