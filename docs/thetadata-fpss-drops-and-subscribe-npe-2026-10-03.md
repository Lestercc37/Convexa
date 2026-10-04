# Theta Terminal: silent FPSS drops, and a NullPointerException on STREAM-add during its own reconnect

Prepared 2026-10-03 for ThetaData support. All timestamps are the Terminal host's local time
(America/New_York) and come from `terminal-latest.log` plus the daily `*-terminal.log.zip`
files on the same machine, cross-checked against our worker's own log. Account/email omitted.

**Setup:** ThetaTerminalv3.jar (file dated 2026-09-26), bundle `STOCK.STANDARD, OPTION.STANDARD,
INDEX.PRO`, Windows 11 on wired Ethernet (Intel I219-LM, 1 Gbps), a single client connected to the
Terminal's local WebSocket (`ws://127.0.0.1:25520/v1/events`). One Terminal for the account.

## Finding 1 - the Terminal's FPSS link drops with no reason in its log

`Connection lost... Attempting to reconnect.` followed by an immediate successful re-login, count per
day (from the daily log zips):

| Day | FPSS "Connection lost" |
|---|---|
| 09-26, 09-27 | 0, 0 |
| 09-28, 09-29, 09-30 | 2, 2, 1 |
| 10-01 | 118 (56 in the 18:xx hour, 21 in 19:xx, 34 in 20:xx - all after the close) |
| 10-02 | 4 (14:48, 16:34, 19:08, 23:03) |
| 10-03 (until 11:05) | 9 |

Saturday 10-03 (market closed, nothing of ours changed): 02:03:26, 02:10:18, 03:20:25, 04:25:33,
07:28:43, 07:37:31, 11:01:36, 11:05:22, plus `MDDS upstream unavailable: io exception` at 09:27:20.

What the log shows around each one: nothing before the `Connection lost` line (no ping/heartbeat/timeout
message), re-login accepted within 0.1-0.9s. One exception: 03:20:25.971
`WARN: [FPSS] Disconnected from server: INVALID_LOGIN_VALUES` right after the re-login attempt, then
`Connection lost` again at 03:20:27.981 and `CONNECTED` at 03:20:28.314.

On our side the host's NIC had no link, DHCP or network-profile events in that window (Windows
event log), and the local WebSocket client is a separate process, so we cannot attribute these drops to
our code. **Question: why is the upstream connection to `nj-a.thetadata.us:20000` being closed this often,
and is there a server-side reason (session handling, maintenance, idle policy) that should appear
in the log?**

## Finding 2 - STREAM-add during the Terminal's reconnect fails with a NullPointerException

Our client reacts to `STATUS: DISCONNECTED` by dropping its local connection, waiting 2s, reconnecting
and re-sending its subscriptions (14 underlyings + 810 option contracts x TRADE and QUOTE = 1,634
`STREAM add` messages). The Terminal begins its own reconnect 1.1-2.0s after it first reports
DISCONNECTED, so a re-subscribe can land in the gap. Two exact cases today:

**04:25:33** (client log / Terminal log)
```
04:25:31.610  client  STATUS DISCONNECTED received
04:25:33.557  Terminal INFO  [FPSS] Connection lost... Attempting to reconnect.
04:25:33.626  client  re-subscribing 810 contracts begins (first request id 14623)
04:25:33.626  Terminal ERROR Invalid WebSocket Message Received:
              {"msg_type": "STREAM", "sec_type": "STOCK", "req_type": "TRADE", "add": true, "id": 14623, "contract": {"root": "SPY"}}
04:25:33.626  Terminal ERROR java.lang.NullPointerException: Cannot invoke
              "net.thetadata.fpssclient.PacketStream.write(net.thetadata.enums.StreamMsgType, byte[], int)"
              because "this.io" is null
04:25:33.813  Terminal INFO  [FPSS] Attempting login
04:25:33.899  Terminal INFO  [FPSS] CONNECTED: [nj-a.thetadata.us:20000]
```
Every one of the 1,634 requests got `{"header": {"type": "REQ_RESPONSE", "status": "CONNECTED",
"response": "ERROR", "req_id": ...}}` (ids 14623-16256; last at 04:25:33.825), i.e. the WebSocket
header said `CONNECTED` while the request failed. Nothing was subscribed afterwards until the next drop
at 07:28:43.

**11:05:22** - the same shape: client got DISCONNECTED at 11:05:20.725, re-subscribed from 11:05:22.746,
Terminal `Connection lost` at 11:05:22.566, login attempt at 11:05:23.423, `CONNECTED` at
11:05:23.639; 1,634 `Invalid WebSocket Message Received` entries (ids 22793-24426), the last
rejection at 11:05:22.937. (From the ~577th occurrence the JVM prints a bare
`java.lang.NullPointerException` without the message, so only the first burst has the
`"this.io" is null` text.)

On the other drops (02:03, 03:20, 07:28, 07:37, 11:01) no rejections reached our log, so the failure
depends on a sub-second timing window. At 02:10:18.346 the Terminal logged a single NullPointerException
for the first request of a burst, then accepted the rest.

**Questions:**
1. Should a STREAM-add received while FPSS is reconnecting be queued, or answered with a specific
   error (not a NullPointerException inside the message handler)? A distinct response would let
   clients retry deliberately.
2. Is the `CONNECTED` in the WebSocket `STATUS`/`REQ_RESPONSE` header meant to mean "the FPSS link
   is up"? In this window it is `CONNECTED` on the REQ_RESPONSE while the link is not.
3. When the Terminal reconnects FPSS by itself, does it keep the subscriptions of already-connected
   WebSocket clients? If yes, our client does not need to tear down its local connection and
   re-subscribe at all.

## What we changed on our side (so this does not depend on an answer)

- A fresh local connection now waits for the Terminal's own `STATUS: CONNECTED` before sending any
  STREAM-add (DISCONNECTED statuses seen during that wait no longer trigger another reconnect).
- After a subscribe burst we count `REQ_RESPONSE` entries that are not `SUBSCRIBED` and re-send
  exactly those (only `response == "ERROR"`), up to 3 rounds, then force one full reconnect, with a
  cap so a persistent rejection cannot cause a reconnect storm.
- Verified against a fake Terminal that reproduces the NPE window over a real local WebSocket: the
  previous client sends 26 rejected requests in that scenario, the new one 0.

Logs for every timestamp above are available on request (`terminal-latest.log`, the daily zips,
and our worker log).

---

## Update 2026-10-04 (add to the report when it is sent)

**Version correlation.** The launcher (`ThetaTerminalv3.jar`, dated 2026-09-26) fetched and started a newer
core on 2026-10-01 12:33: `lib/202609241.jar` -> `lib/202609301.jar` (Terminal reports `20260930:18f1199`).
FPSS "Connection lost" per day: 09-26 0, 09-27 0, 09-28 2, 09-29 2, 09-30 1 | **10-01 118** (burst from 18:14,
after the close) | 10-02 4 | 10-03 13 | 10-04 until 11:00: 4. This is a correlation only (the pairs of drops
4-9 minutes apart already existed on 09-28/09-29). Intervals between drops range from 4 minutes to 15 hours; no
fixed period. **Questions: what changed in 202609301 around FPSS? Is there a supported way to pin the core
version (the launcher's JarLibraryManager re-fetches the latest on every start)?**

**One long outage.** 2026-10-03 15:03:40.928 `Connection lost`, next `Attempting login` only at 15:06:16.265
(2 min 35 s). Every other drop re-logged in within 0.1-0.9 s. On 2026-10-04 08:11:47.560 the re-login took 4.4 s and
connected to `nj-b.thetadata.us:20000`; all others reconnected to `nj-a`.

**Cold start.** After a Terminal restart the log shows `Starting server at ...` but no `[FPSS]` line until a
WebSocket client sends its first `STREAM add`; until then every `STATUS` frame says `DISCONNECTED`. Measured
2026-10-03 12:51-12:52: a client that connected and stayed silent saw DISCONNECTED indefinitely; one STREAM add
later FPSS logged in (12:52:28), STATUS became CONNECTED about 1 s later, and the request was answered
SUBSCRIBED. **Question: is login-on-first-subscription intended, and is `STATUS` meant to reflect FPSS state?**
(It broke a client that waited for CONNECTED before subscribing.)

**After the client-side fixes (2026-10-03 11:13 on).** Natural drops at 11:34, 11:39, 12:21 and 13:55, 14:04,
15:03 were handled with zero rejected subscriptions; the 11:05 and 04:25 rejections above happened before the
fix. A deliberate 3-minute Terminal stop on 2026-10-04 (11:13-11:16) recovered by itself in about 80 s.
