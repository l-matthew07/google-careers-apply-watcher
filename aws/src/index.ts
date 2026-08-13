/**
 * AWS Lambda: Notion careers (Ashby) posting watcher with Photon Spectrum
 * iMessage alerts.
 *
 * Runs on an EventBridge schedule. Reads the Notion board from two redundant
 * sources — Ashby's public job-board API and the server-rendered board page
 * (union wins for "live"; "closed" requires absence from both). When a
 * watched posting goes live
 * (listed on the board) it DMs every number in RECIPIENTS over iMessage via
 * Spectrum Cloud with the direct application link. A posting that was live
 * and later disappears triggers a one-time "closed" alert. Already-notified
 * events are remembered in an SSM parameter so you get exactly one message
 * per event.
 *
 * Environment variables:
 *     TITLE_PATTERN   case-insensitive regex; any board posting whose title
 *                     matches is watched (catches postings that don't exist
 *                     yet, e.g. future intern roles)
 *     JOB_URLS        optional comma- or newline-separated Ashby posting URLs
 *                     (https://jobs.ashbyhq.com/notion/<uuid>) to watch by id
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

const BOARD_API = "https://api.ashbyhq.com/posting-api/job-board/notion";
const BOARD_PAGE = "https://jobs.ashbyhq.com/notion";
const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";

const ssm = new SSMClient({});
const ses = new SESv2Client({});

type Status = "live" | "waiting" | "gone" | "error";
// titles remembers pattern-discovered postings (url -> title) so we can
// name them in "closed" alerts after they've been delisted.
type State = { notified: string[]; titles?: Record<string, string> };
type AshbyJob = {
  id: string;
  title: string;
  location?: string;
  jobUrl: string;
  applyUrl: string;
};

function jobIdFromUrl(url: string): string | null {
  const m = url.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
  return m?.[1]?.toLowerCase() ?? null;
}

async function fetchBoard(): Promise<Map<string, AshbyJob> | null> {
  try {
    const resp = await fetch(BOARD_API, {
      headers: { "User-Agent": UA, Accept: "application/json" },
      signal: AbortSignal.timeout(25_000),
    });
    if (!resp.ok) return null;
    const data = (await resp.json()) as { jobs?: AshbyJob[] };
    return new Map((data.jobs ?? []).map(j => [j.id.toLowerCase(), j]));
  } catch {
    return null;
  }
}

// Redundant second source: the board page itself server-renders the job list
// as JSON in the HTML. If the posting API's cache ever lags, this is where a
// new posting shows up first (it's what a human visiting the page sees).
async function fetchBoardPage(): Promise<Map<string, AshbyJob> | null> {
  try {
    const resp = await fetch(BOARD_PAGE, {
      headers: { "User-Agent": UA },
      signal: AbortSignal.timeout(25_000),
    });
    if (!resp.ok) return null;
    const html = await resp.text();
    const re = /\{"id":"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})","title":"((?:[^"\\]|\\.)*)"(?:[^{}]*?"locationName":"((?:[^"\\]|\\.)*)")?/gi;
    const jobs = new Map<string, AshbyJob>();
    for (const m of html.matchAll(re)) {
      const id = m[1]!.toLowerCase();
      jobs.set(id, {
        id,
        title: JSON.parse(`"${m[2]}"`),
        location: m[3] ? JSON.parse(`"${m[3]}"`) : undefined,
        jobUrl: `${BOARD_PAGE}/${id}`,
        applyUrl: `${BOARD_PAGE}/${id}/application`,
      });
    }
    // An empty map means the page markup changed and the regex found
    // nothing — treat as a source failure, not an empty board.
    return jobs.size > 0 ? jobs : null;
  } catch {
    return null;
  }
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
          Subject: { Data: "Notion careers apply-watcher alert" },
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

  const [apiBoard, pageBoard] = await Promise.all([
    fetchBoard(),
    fetchBoardPage(),
  ]);
  // Union of both sources; API entries win (richer applyUrl/location data).
  const board =
    apiBoard === null && pageBoard === null
      ? null
      : new Map([...(pageBoard ?? []), ...(apiBoard ?? [])]);
  const bothSourcesOk = apiBoard !== null && pageBoard !== null;
  if (board) {
    const apiIds = new Set(apiBoard?.keys() ?? []);
    const pageIds = new Set(pageBoard?.keys() ?? []);
    const onlyApi = [...apiIds].filter(id => pageBoard && !pageIds.has(id));
    const onlyPage = [...pageIds].filter(id => apiBoard && !apiIds.has(id));
    console.log(
      `board: api=${apiBoard?.size ?? "FAIL"} page=${pageBoard?.size ?? "FAIL"}` +
      (onlyApi.length || onlyPage.length
        ? ` DIVERGED onlyApi=[${onlyApi}] onlyPage=[${onlyPage}]`
        : ""),
    );
  }

  for (const url of urls) {
    const id = jobIdFromUrl(url);
    if (!id || board === null) {
      results[url.slice(0, 80)] = "error";
      continue;
    }
    const job = board.get(id);
    const keyLive = `live:${url}`;
    const keyGone = `gone:${url}`;

    if (job) {
      results[job.title] = "live";
      if (!state.notified.includes(keyLive)) {
        alerts.push(
          `🚨 Notion posting is LIVE: ${job.title}` +
          (job.location ? ` (${job.location})` : "") +
          `\nApply now: ${job.applyUrl ?? job.jobUrl}`,
        );
        state.notified.push(keyLive);
      }
      // Clear any prior "gone" mark so a future close re-alerts.
      state.notified = state.notified.filter(k => k !== keyGone);
    } else if (state.notified.includes(keyLive) && bothSourcesOk) {
      // Absent from BOTH sources after being live: posting closed. (With
      // one source down we can't be sure, so we wait for the next run.)
      results[id] = "gone";
      if (!state.notified.includes(keyGone)) {
        alerts.push(`Notion posting closed (unlisted): ${url}`);
        state.notified.push(keyGone);
      }
      // Clear the "live" mark so a reopen re-alerts.
      state.notified = state.notified.filter(k => k !== keyLive);
    } else {
      // Not listed yet — keep waiting for it to (re)open.
      results[id] = "waiting";
    }
  }

  // --- Title-pattern watch: catches postings that don't exist yet ----------
  const pattern = process.env.TITLE_PATTERN
    ? new RegExp(process.env.TITLE_PATTERN, "i")
    : null;
  if (pattern && board) {
    state.titles ??= {};
    for (const job of board.values()) {
      if (!pattern.test(job.title)) continue;
      const keyLive = `live:${job.jobUrl}`;
      const keyGone = `gone:${job.jobUrl}`;
      results[job.title] = "live";
      state.titles[job.jobUrl] = job.title;
      if (!state.notified.includes(keyLive)) {
        alerts.push(
          `🚨 Notion posting is LIVE: ${job.title}` +
          (job.location ? ` (${job.location})` : "") +
          `\nApply now: ${job.applyUrl ?? job.jobUrl}`,
        );
        state.notified.push(keyLive);
      }
      state.notified = state.notified.filter(k => k !== keyGone);
    }
    // Pattern-discovered postings that have since been delisted (only
    // trustworthy when both sources answered).
    for (const [url, title] of Object.entries(state.titles)) {
      const id = jobIdFromUrl(url);
      if (!id || board.has(id) || urls.includes(url) || !bothSourcesOk) continue;
      results[title] = "gone";
      const keyLive = `live:${url}`;
      const keyGone = `gone:${url}`;
      if (state.notified.includes(keyLive) && !state.notified.includes(keyGone)) {
        alerts.push(`Notion posting closed (unlisted): ${title}\n${url}`);
        state.notified.push(keyGone);
        state.notified = state.notified.filter(k => k !== keyLive);
      }
    }
    console.log(`pattern /${process.env.TITLE_PATTERN}/i matched ` +
      `${Object.values(results).filter(s => s === "live").length} live posting(s) ` +
      `of ${board.size} on the board`);
  }

  if (alerts.length > 0) {
    await emailAlerts(alerts);
    await sendAlerts(alerts);
  }
  await saveState(param, state);
  console.log(JSON.stringify(results));
  return results;
};
