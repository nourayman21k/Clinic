import http from 'k6/http';
import crypto from 'k6/crypto';
import { check } from 'k6';
import { Counter, Trend } from 'k6/metrics';

// Load test for the clinic backend's WhatsApp webhook receiver
// (app/whatsapp_agent/webhook.py). Goal: find where this system's OWN
// concurrency ceiling actually sits -- the built-in semaphore
// (WHATSAPP_AGENT_CONCURRENCY, default 5) and, one layer downstream, Groq's
// real free-tier rate limit (30 requests/minute) -- by generating enough
// distinct simulated WhatsApp conversations in a short window to exceed both.
//
// Deliberately bounded to 45 total requests (not thousands): each request
// that reaches a NEW synthetic chat_id costs exactly one real Groq API call
// (the route_message classifier), so this stays a small fraction of Groq's
// free-tier daily quota (1,000 req/day) while still comfortably exceeding
// both the 30/min ceiling and the 5-concurrent semaphore within the run.

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8001';
const WEBHOOK_SECRET = __ENV.WEBHOOK_SECRET;
const SESSION_ID = __ENV.SESSION_ID || '50e2cc38-c65c-482b-8d21-50c2fb67dffc';

if (!WEBHOOK_SECRET) {
  throw new Error('Set WEBHOOK_SECRET env var (WHATSAPP_WEBHOOK_SECRET from .env)');
}

const ackLatency = new Trend('webhook_ack_latency', true);
const acceptedCount = new Counter('webhook_accepted');
const rejectedCount = new Counter('webhook_rejected');

export const options = {
  scenarios: {
    // shared-iterations: precise total volume (protects the real Groq quota)
    // while still ramping concurrency up to `vus` in-flight at once.
    webhook_burst: {
      executor: 'shared-iterations',
      vus: 25,
      iterations: 45,
      maxDuration: '90s',
    },
  },
  thresholds: {
    // The ACK layer (signature check + idempotency claim + BackgroundTasks
    // schedule) must stay fast regardless of downstream LLM load -- this is
    // what actually matters for OpenWA's 10s WEBHOOK_TIMEOUT.
    webhook_ack_latency: ['p(95)<2000'],
    webhook_rejected: ['count==0'],
  },
};

function hmacSign(rawBody) {
  return 'sha256=' + crypto.hmac('sha256', WEBHOOK_SECRET, rawBody, 'hex');
}

export default function () {
  const uniqueId = `${__VU}_${__ITER}_${Date.now()}`;
  const chatId = `20100${String(__VU).padStart(3, '0')}${String(__ITER).padStart(3, '0')}@c.us`; // distinct fake "patient" per VU+iteration

  const payload = {
    event: 'message.received',
    timestamp: new Date().toISOString(),
    sessionId: SESSION_ID,
    idempotencyKey: `k6load_${uniqueId}`,
    deliveryId: `dlv_k6_${uniqueId}`,
    data: {
      id: `K6MSG_${uniqueId}`,
      from: chatId,
      chatId: chatId,
      type: 'text',
      body: 'مرحبا', // cheap "direct reply" path -- one Groq classify call, no booking flow
      fromMe: false,
      isGroup: false,
    },
  };

  const rawBody = JSON.stringify(payload);
  const signature = hmacSign(rawBody);

  const res = http.post(`${BASE_URL}/api/whatsapp/webhook`, rawBody, {
    headers: {
      'Content-Type': 'application/json',
      'X-OpenWA-Signature': signature,
    },
  });

  ackLatency.add(res.timings.duration);

  const ok = check(res, {
    'status is 200': (r) => r.status === 200,
    'status is accepted': (r) => {
      try {
        return JSON.parse(r.body).status === 'accepted';
      } catch {
        return false;
      }
    },
  });

  if (ok) {
    acceptedCount.add(1);
  } else {
    rejectedCount.add(1);
    console.error(`Unexpected response: ${res.status} ${res.body}`);
  }
}
