const DECISION = {
  KEEP: "保留",
  ROLLBACK: "回滚",
  "BACK-OFF": "证据不足",
  ESCALATE: "升级",
  SEED: "种子",
  NONE: "观察",
};

let busy = false;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function pct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${Math.round(n * 100)}%`;
}

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `请求失败 ${response.status}`);
  }
  return data;
}

function signalLine(signal) {
  const labels = (signal.labels || []).join(" / ") || "无标签";
  const comment = signal.sample_comment
    ? `<div>评论：${esc(signal.sample_comment)}</div>`
    : "";
  return `<li>
    <strong>${esc(signal.repo)}</strong> · ${esc(signal.id)} · 反应 ${esc(signal.reactions)} · 评论 ${esc(signal.comment_count)}
    <div>${esc(signal.title)}</div>
    <div>${esc(labels)} · ${esc(signal.time)}</div>
    ${comment}
  </li>`;
}

function card(item) {
  const kind = item.kind === "real" ? "真实需求" : "伪需求";
  const notes = (item.notes || []).map((note) => `<li>补充：${esc(note)}</li>`).join("");
  const noteBlock = notes ? `<ul class="evidence">${notes}</ul>` : "";
  return `<article class="card" data-id="${esc(item.id)}">
    <div class="badges">
      <span class="badge ${esc(item.kind)}">${kind}</span>
      <span class="badge">置信 ${pct(item.confidence)}</span>
      <span class="badge">优先级 ${Number(item.priority || 0).toFixed(2)}</span>
      <span class="badge">策略 v${esc(item.policy_version)} ${esc(item.policy_name)}</span>
    </div>
    <h3>${esc(item.description)}</h3>
    <p class="why">${esc(item.why)}</p>
    <ol class="evidence">${(item.evidence || []).map(signalLine).join("")}</ol>
    ${noteBlock}
    <div class="actions">
      <button type="button" data-act="confirm">确认</button>
      <button type="button" data-act="deny" class="secondary">否认</button>
      <button type="button" data-act="supplement" class="ghost">补充</button>
    </div>
    <form class="supplement" hidden>
      <input name="note" maxlength="2000" placeholder="补充一条证据或纠正" required>
      <button type="submit">提交补充</button>
    </form>
  </article>`;
}

function renderInsights(data) {
  const insights = document.querySelector("#insights");
  const forgotten = document.querySelector("#forgotten");
  insights.innerHTML = (data.insights || []).map(card).join("") || `<p class="empty">没有已晋升的假设。</p>`;
  forgotten.innerHTML = (data.forgotten || []).map(card).join("") || `<p class="empty">没有被策略放下的信号簇。</p>`;
  const decision = data.decision || "";
  const status = document.querySelector("#status");
  status.className = `status ${decision}`;
  const metrics = data.metrics || {};
  const agreement = metrics.labeled
    ? `当前同意率 ${pct(metrics.agreement)}，基线 ${pct(metrics.base_agreement)}，留出 ${pct(metrics.heldout)}。`
    : "";
  status.textContent = `${data.reason || DECISION[decision] || "就绪"}${agreement}`;
  const knobs = data.knobs || {};
  document.querySelector("#knobs").innerHTML = `
    <strong>策略 v${esc(data.policy_version)} · ${esc(data.policy_name || knobs.name || "")}</strong>
    <span>最少证据 ${esc(knobs.min_support)}</span>
    <span>问句当伪需求 ${knobs.question_fake ? "是" : "否"}</span>
    <span>反应权重 ${esc(knobs.reaction_bias)}</span>
    <span>发布途径 ${esc(data.publish_via || "—")}</span>`;
  const trials = document.querySelector("#trials");
  const rows = data.trials || [];
  trials.innerHTML = rows.length
    ? rows.map((trial) => `<li>
        <span class="tag ${esc(trial.decision)}">${esc(DECISION[trial.decision] || trial.decision)}</span>
        ${esc(trial.arm)} · 同意率 ${pct(trial.agreement)} / 基线 ${pct(trial.base)}
        · ${esc(trial.via)} · 隔离 ${trial.isolated ? "是" : "否"}
        <div>${esc(trial.reason || "")}</div>
      </li>`).join("")
    : `<li class="empty">这一轮没有试策略变体。</li>`;
}

function renderPolicy(policy) {
  const current = policy.current || {};
  const events = document.querySelector("#events");
  const items = policy.events || [];
  const currentId = [...items].reverse().find((event) => event.version === current.version)?.id;
  events.innerHTML = items.length
    ? items.map((event) => {
        const currentMark = event.id === currentId ? " · 当前" : "";
        const button = event.version === current.version
          ? ""
          : `<button type="button" class="ghost" data-rollback="${esc(event.version)}">回滚到 v${esc(event.version)}</button>`;
        return `<li>
          <span class="tag ${esc(event.decision)}">${esc(DECISION[event.decision] || event.decision)}</span>
          v${esc(event.version)} ${esc(event.name)} ${esc(event.arm)}${currentMark}
          <div>${esc(event.reason)}</div>
          <div>${esc(event.created_at || "")}</div>
          ${button}
        </li>`;
      }).join("")
    : `<li class="empty">还没有策略事件。</li>`;
}

function renderAudit(payload) {
  const audit = document.querySelector("#audit");
  const rows = payload.audit || [];
  audit.innerHTML = rows.slice(0, 12).map((row) => `<li>
    <span class="tag ${esc(row.decision || "")}">${esc(row.kind || "")}</span>
    ${esc(row.decision || "")} ${esc(row.arm || row.name || "")}
    <div>${esc(row.reason || row.via || "")}</div>
  </li>`).join("") || `<li class="empty">没有审计记录。</li>`;
}

async function refresh() {
  const [insights, policy, audit] = await Promise.all([
    api("/api/insights"),
    api("/api/policy"),
    api("/api/audit"),
  ]);
  renderInsights(insights);
  renderPolicy(policy);
  renderAudit(audit);
}

async function sendFeedback(id, verdict, note) {
  if (busy) return;
  busy = true;
  document.body.querySelectorAll("button").forEach((button) => {
    button.disabled = true;
  });
  try {
    await api("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ insight_id: id, verdict, note: note || "" }),
    });
    await refresh();
  } catch (error) {
    document.querySelector("#status").textContent = error.message;
  } finally {
    busy = false;
    document.body.querySelectorAll("button").forEach((button) => {
      button.disabled = false;
    });
  }
}

document.body.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.id === "reset") {
    if (!window.confirm("清空反馈和策略，重新装入示例 issue？")) return;
    busy = true;
    api("/api/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then(() => refresh())
      .catch((error) => {
        document.querySelector("#status").textContent = error.message;
      })
      .finally(() => {
        busy = false;
      });
    return;
  }
  const rollback = button.dataset.rollback;
  if (rollback) {
    if (!window.confirm(`回滚到策略版本 ${rollback}？`)) return;
    busy = true;
    api("/api/rollback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: Number(rollback) }),
    })
      .then(() => refresh())
      .catch((error) => {
        document.querySelector("#status").textContent = error.message;
      })
      .finally(() => {
        busy = false;
      });
    return;
  }
  const card = button.closest(".card");
  if (!card) return;
  if (button.dataset.act === "supplement") {
    card.querySelector(".supplement").hidden = false;
    card.querySelector("input").focus();
    return;
  }
  if (button.dataset.act === "confirm" || button.dataset.act === "deny") {
    sendFeedback(card.dataset.id, button.dataset.act, "");
  }
});

document.body.addEventListener("submit", (event) => {
  const form = event.target.closest(".supplement");
  if (!form) return;
  event.preventDefault();
  const card = form.closest(".card");
  const note = new FormData(form).get("note");
  sendFeedback(card.dataset.id, "supplement", String(note || ""));
});

refresh().catch((error) => {
  document.querySelector("#status").textContent = error.message;
});
