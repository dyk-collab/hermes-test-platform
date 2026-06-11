"use strict";

// ---- 小工具 ----------------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};
const api = async (url, opts) => {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
  return r.json();
};
const fmtSecs = (v) => (typeof v === "number" ? `${v.toFixed(1)}s` : "-");
const fmtCost = (m) => {
  if (!m) return "-";
  if (m.cost_status === "unknown" || m.cost == null) return "未知";
  return `$${Number(m.cost).toFixed(4)}`;
};
const setHint = (t) => ($("#conn-hint").textContent = t);

let currentRunId = null;
let META = { toolsets: [], grader_kinds: [], case_templates: [] };
let PRESETS = []; // 缓存的 runner 预设列表
let currentPreset = null; // 预设页正在编辑的预设名

// ---- 视图导航 --------------------------------------------------------------
function showView(name) {
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
}
$("#nav").addEventListener("click", (e) => {
  const btn = e.target.closest(".nav-btn");
  if (btn) showView(btn.dataset.view);
});

// ---- meta（驱动引导面板）---------------------------------------------------
async function loadMeta() {
  try {
    META = await api("/api/meta");
    renderGuide();
  } catch (e) {
    setHint("加载 meta 失败：" + e.message);
  }
}

// ---- 数据集下拉（评测视图）-------------------------------------------------
async function loadDatasets() {
  try {
    const { datasets } = await api("/api/datasets");
    const sel = $("#dataset-select");
    sel.innerHTML = "";
    if (!datasets.length) {
      sel.appendChild(el("option", null, "（datasets/ 下无数据集）"));
      return;
    }
    datasets.forEach((d) => {
      const o = el("option", null, d);
      o.value = d;
      sel.appendChild(o);
    });
  } catch (e) {
    setHint("加载数据集失败：" + e.message);
  }
}

// ---- 历史运行 --------------------------------------------------------------
async function loadRuns() {
  try {
    const { runs } = await api("/api/runs");
    const list = $("#runs-list");
    list.innerHTML = "";
    if (!runs.length) {
      list.appendChild(el("li", "muted", "暂无历史运行"));
      return;
    }
    runs.forEach((r) => {
      const li = el("li");
      if (r.run_id === currentRunId) li.classList.add("active");
      li.appendChild(el("div", "run-id", r.run_id));
      const sub = el("div", "run-sub");
      const rate =
        r.pass_rate != null ? `${Math.round(r.pass_rate * 100)}% (${r.passed}/${r.total})` : "未打分";
      sub.appendChild(el("span", null, r.model || "默认模型"));
      sub.appendChild(el("span", null, rate));
      li.appendChild(sub);
      li.onclick = () => openRun(r.run_id);
      list.appendChild(li);
    });
  } catch (e) {
    setHint("加载历史运行失败：" + e.message);
  }
}

// ---- 报告渲染（历史视图）---------------------------------------------------
async function openRun(runId) {
  currentRunId = runId;
  showView("history");
  await loadRuns(); // 更新左栏高亮
  try {
    const report = await api(`/api/runs/${runId}`);
    renderReport(report);
  } catch (e) {
    setHint("该运行尚未打分或读取失败：" + e.message);
  }
}

function renderReport(report) {
  $("#history-empty").classList.add("hidden");
  $("#report-panel").classList.remove("hidden");

  const man = report.manifest || {};
  const sum = report.summary || {};
  $("#report-title").textContent = `报告 · ${man.run_id || ""}`;
  $("#report-meta").textContent =
    `数据集 ${man.dataset || "?"} · 预设 ${man.preset || "默认"} · 模型 ${man.model || "默认模型"} · ${man.started_at || ""}`;

  const rate = Math.round((sum.pass_rate || 0) * 100);
  const summary = $("#report-summary");
  summary.innerHTML = "";
  const stat = (num, lbl) => {
    const s = el("div", "stat");
    s.appendChild(el("div", "num", num));
    s.appendChild(el("div", "lbl", lbl));
    return s;
  };
  summary.appendChild(stat(`${sum.passed || 0}/${sum.total || 0}`, "通过 / 总数"));
  summary.appendChild(stat(`${rate}%`, "通过率"));
  Object.entries(sum.by_type || {}).forEach(([t, b]) =>
    summary.appendChild(stat(`${b.passed}/${b.total}`, `类型：${t}`))
  );

  const tbody = $("#cases-tbody");
  tbody.innerHTML = "";
  const graderLabel = (g) => {
    let label = `${g.kind}${g.passed ? "✓" : "✗"}`;
    if (g.kind === "llm_judge" && g.details?.score_10 != null) {
      label += ` ${Number(g.details.score_10).toFixed(1)}/10`;
    }
    return label;
  };
  (report.cases || []).forEach((c) => {
    const tr = el("tr");
    tr.onclick = () => openTrajectory(currentRunId, c.case_id);
    const m = c.metrics || {};
    const graders = (c.grades || [])
      .map((g) => `<span class="grader-tag ${g.passed ? "ok" : "bad"}">${graderLabel(g)}</span>`)
      .join("");
    tr.innerHTML = `
      <td><b>${c.case_id}</b></td>
      <td>${c.type || "-"}</td>
      <td><span class="pill ${c.passed ? "pass" : "fail"}">${c.passed ? "通过" : "失败"}</span></td>
      <td>${graders || "-"}</td>
      <td>${fmtSecs(m.wall_clock)}</td>
      <td>${m.tool_calls ?? "-"}</td>
      <td>${m.api_calls ?? "-"}</td>
      <td>${m.total_tokens ?? "-"}</td>
      <td>${fmtCost(m)}</td>`;
    tbody.appendChild(tr);
  });
}

// ---- 消息气泡渲染（轨迹抽屉 & 试跑共用）------------------------------------
function renderMessagesInto(box, messages, onAssert) {
  box.innerHTML = "";
  (messages || []).forEach((msg) => {
    const role = msg.role === "user" ? "user" : msg.role === "tool" ? "tool" : "assistant";
    const wrap = el("div", `msg ${role}`);
    const who = role === "user" ? "用户" : role === "tool" ? `工具结果 · ${msg.tool_name || ""}` : "助手";
    wrap.appendChild(el("div", "who", who));
    if (msg.content) wrap.appendChild(el("div", "body", msg.content));
    (msg.tool_calls || []).forEach((tc) => {
      const tcEl = el("div", "toolcall");
      tcEl.appendChild(el("span", "tc-text", `→ ${tc.name}(${JSON.stringify(tc.arguments)})`));
      if (onAssert) {
        const b = el("button", "btn tiny", "＋断言");
        b.title = "把这次调用加为 tool_call 断言";
        b.onclick = (e) => {
          e.stopPropagation();
          onAssert(tc);
        };
        tcEl.appendChild(b);
      }
      wrap.appendChild(tcEl);
    });
    box.appendChild(wrap);
  });
}

// ---- 轨迹抽屉（历史报告）---------------------------------------------------
async function openTrajectory(runId, caseId) {
  try {
    const t = await api(`/api/runs/${runId}/cases/${caseId}`);
    renderTrajectory(t);
    $("#drawer").classList.remove("hidden");
    $("#drawer-overlay").classList.remove("hidden");
  } catch (e) {
    setHint("读取轨迹失败：" + e.message);
  }
}

function closeDrawer() {
  $("#drawer").classList.add("hidden");
  $("#drawer-overlay").classList.add("hidden");
}

// B 轨：grader 从裸 JSON 改为结构化卡片。
// 兼容两种输入：① 打分结果（含 passed/score/reason/details）② 用例 grader 配置（kind + 字段）。
const _GRADER_RESULT_KEYS = new Set(["kind", "passed", "score", "reason", "details"]);
const _JUDGE_PROCESS_KEYS = new Set(["judge_prompt", "judge_raw", "judge_stderr"]);
function renderGraderCard(g) {
  const card = el("div", "grader-card");
  const head = el("div", "grader-card-head");
  head.appendChild(el("span", "grader-kind", g.kind || "?"));
  if (typeof g.passed === "boolean")
    head.appendChild(el("span", `pill ${g.passed ? "pass" : "fail"}`, g.passed ? "通过" : "失败"));
  if (g.score != null) head.appendChild(el("span", "grader-score", `score ${Number(g.score).toFixed(2)}`));
  card.appendChild(head);
  if (g.reason) card.appendChild(el("div", "grader-reason", g.reason));

  // 打分结果的 details；或配置态的非 kind 字段，二者都用 pre 展示
  const rawDetails = g.details && Object.keys(g.details).length ? g.details : null;
  const details = rawDetails ? Object.fromEntries(
    Object.entries(rawDetails).filter(([k]) => !_JUDGE_PROCESS_KEYS.has(k))
  ) : null;
  const config = {};
  Object.entries(g).forEach(([k, v]) => {
    if (!_GRADER_RESULT_KEYS.has(k)) config[k] = v;
  });
  const blob = details && Object.keys(details).length ? details : (Object.keys(config).length ? config : null);
  if (blob) {
    const d = el("details", "grader-details");
    d.appendChild(el("summary", null, rawDetails ? "评判结果" : "配置"));
    d.appendChild(el("pre", null, JSON.stringify(blob, null, 2)));
    card.appendChild(d);
  }
  if (rawDetails?.judge_prompt || rawDetails?.judge_raw || rawDetails?.judge_stderr) {
    const d = el("details", "grader-details grader-process");
    d.appendChild(el("summary", null, "评判过程"));
    if (rawDetails.judge_prompt) {
      d.appendChild(el("div", "grader-process-label", "Judge prompt"));
      d.appendChild(el("pre", null, rawDetails.judge_prompt));
    }
    if (rawDetails.judge_raw) {
      d.appendChild(el("div", "grader-process-label", "Judge raw output"));
      d.appendChild(el("pre", null, rawDetails.judge_raw));
    }
    if (rawDetails.judge_stderr) {
      d.appendChild(el("div", "grader-process-label", "Judge stderr"));
      d.appendChild(el("pre", null, rawDetails.judge_stderr));
    }
    card.appendChild(d);
  }
  return card;
}

function renderTrajectory(t) {
  $("#drawer-title").textContent = `轨迹 · ${t.case_id}`;
  const m = t.metrics || {};
  $("#drawer-meta").textContent =
    `session ${t.session_id || "-"} · 模型 ${m.model || "?"} · 耗时 ${fmtSecs(m.wall_clock)} · ` +
    `工具 ${m.tool_calls ?? "-"} · API ${m.api_calls ?? "-"} · tokens ${m.total_tokens ?? "-"} · 成本 ${fmtCost(m)}`;

  const gbox = $("#drawer-graders");
  gbox.innerHTML = "";
  // graders 数组在历史报告里来自 graded 明细（含 passed/score/reason）；
  // 若是 case 的 grader spec（无打分），也兜底展示 kind。
  (t.graders || []).forEach((g) => gbox.appendChild(renderGraderCard(g)));

  const box = $("#drawer-messages");
  box.innerHTML = "";
  if (t.error) {
    const e = el("div", "msg tool");
    e.appendChild(el("div", "who", "运行错误"));
    e.appendChild(el("div", "body", t.error));
    box.appendChild(e);
  }
  if (t.diagnostics) {
    const e = el("div", "msg tool");
    e.appendChild(el("div", "who", "Hermes 诊断日志"));
    e.appendChild(el("pre", "body", t.diagnostics));
    box.appendChild(e);
  }
  renderMessagesInto(box, t.messages, null);
}

// ---- 评测：触发运行 + 实时进度（增量日志）---------------------------------
let pollTimer = null;
let renderedEvents = 0; // B 轨：增量 append，不再每轮全量重建
let activeJobId = null;

function eventLine(ev) {
  let line = "";
  if (ev.type === "run_start") line = `开始运行 ${ev.total} 条用例 → ${ev.run_id}`;
  else if (ev.type === "case_start") line = `[${ev.i}/${ev.total}] ${ev.case_id} …`;
  else if (ev.type === "case_done")
    line = ev.ok
      ? `[${ev.i}/${ev.total}] ${ev.case_id} ✓ ${fmtSecs(ev.wall_clock)} · ${ev.session_id}`
      : `[${ev.i}/${ev.total}] ${ev.case_id} ✗ 运行错误：${ev.error}`;
  else if (ev.type === "grade_start") line = `开始打分（${ev.total} 条）…`;
  else if (ev.type === "case_graded") line = `打分 ${ev.case_id}：${ev.passed ? "通过" : "失败"}`;
  else if (ev.type === "graded") line = `打分完成，报告已生成。`;
  else if (ev.type === "cancel_requested") line = `已请求停止，正在结束运行中的用例…`;
  else if (ev.type === "run_cancelled") line = `已停止：完成 ${ev.completed}/${ev.total} 条用例。`;
  else if (ev.type === "grade_cancelled") line = `打分已停止：完成 ${ev.completed}/${ev.total} 条。`;
  else if (ev.type === "error") line = `错误：${ev.message}`;
  else return null; // try_start/try_done 等不进运行日志
  const cls =
    ev.type === "error" || (ev.type === "case_done" && !ev.ok)
      ? "err"
      : ev.type === "case_done" || ev.type === "graded"
      ? "ok"
      : "dim";
  return { line, cls };
}

function renderProgress(job) {
  const panel = $("#progress-panel");
  panel.classList.remove("hidden");
  $("#eval-empty").classList.add("hidden");
  const badge = $("#progress-status");
  badge.className = "badge " + job.status;
  badge.textContent =
    { running: "运行中", cancelling: "停止中", cancelled: "已停止", done: "已完成", error: "出错" }[job.status] ||
    job.status;

  const log = $("#progress-log");
  const events = job.events || [];
  for (let i = renderedEvents; i < events.length; i++) {
    const r = eventLine(events[i]);
    if (r) log.appendChild(el("div", r.cls, r.line));
  }
  renderedEvents = events.length;
  log.scrollTop = log.scrollHeight;
}

async function pollJob(jobId, onDone) {
  try {
    const job = await api(`/api/jobs/${jobId}`);
    if (onDone) onDone(job, false);
    else renderProgress(job);
    if (job.status === "running" || job.status === "cancelling") {
      pollTimer = setTimeout(() => pollJob(jobId, onDone), 1000);
      return;
    }
    if (onDone) {
      onDone(job, true);
      return;
    }
    activeJobId = null;
    setStopVisible(false);
    setHint(job.status === "done" ? "完成" : job.status === "cancelled" ? "已停止" : "出错");
    enableButtons(true);
    await loadRuns();
    const target = job.run_id || currentRunId;
    if (target && job.status === "done") await openRun(target);
  } catch (e) {
    activeJobId = null;
    setStopVisible(false);
    setHint("轮询任务失败：" + e.message);
    enableButtons(true);
  }
}

function enableButtons(on) {
  $("#run-btn").disabled = !on;
  $("#regrade-btn").disabled = !on;
}

function setStopVisible(on) {
  $("#stop-btn").classList.toggle("hidden", !on);
  $("#stop-btn").disabled = !on;
}

// 把选中的预设 + 模型覆盖解析成 /api/run 的请求体
function resolveRunConfig(dataset) {
  const presetName = $("#preset-select").value;
  const p = PRESETS.find((x) => x.name === presetName) || {};
  const modelOverride = $("#model-input").value.trim();
  return {
    dataset,
    preset: presetName || null,
    model: modelOverride || p.model || null,
    provider: p.provider || null,
    profile: p.profile || null,
    toolsets: p.toolsets || null,
    max_turns: p.max_turns ?? null,
    timeout: p.timeout ?? 600,
    yolo: p.yolo !== undefined ? p.yolo : true,
    accept_hooks: p.accept_hooks !== undefined ? p.accept_hooks : true,
    ignore_rules: p.ignore_rules !== undefined ? p.ignore_rules : true,
    concurrency: Math.max(1, Number($("#concurrency-input").value || 1)),
  };
}

async function startRun() {
  const dataset = $("#dataset-select").value;
  if (!dataset) return setHint("请先选择数据集");
  enableButtons(false);
  setHint("正在发起评测…");
  $("#progress-log").innerHTML = "";
  renderedEvents = 0;
  try {
    const { job_id } = await api("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(resolveRunConfig(dataset)),
    });
    activeJobId = job_id;
    setStopVisible(true);
    pollJob(job_id);
  } catch (e) {
    setHint("发起失败：" + e.message);
    enableButtons(true);
  }
}

async function regrade() {
  if (!currentRunId) return;
  enableButtons(false);
  setHint("正在重新打分…");
  $("#progress-log") && ($("#progress-log").innerHTML = "");
  renderedEvents = 0;
  try {
    const { job_id } = await api(`/api/runs/${currentRunId}/grade`, { method: "POST" });
    activeJobId = job_id;
    setStopVisible(true);
    pollJob(job_id);
  } catch (e) {
    setHint("重新打分失败：" + e.message);
    enableButtons(true);
  }
}

async function cancelActiveJob() {
  if (!activeJobId) return;
  $("#stop-btn").disabled = true;
  setHint("正在请求停止…");
  try {
    await api(`/api/jobs/${activeJobId}/cancel`, { method: "POST" });
  } catch (e) {
    $("#stop-btn").disabled = false;
    setHint("停止失败：" + e.message);
  }
}

// ============================================================================
//  数据集视图：引导式 YAML 构建
// ============================================================================
let currentDataset = null; // 当前编辑的数据集文件名
let validateTimer = null;

async function loadDatasetFiles() {
  try {
    const { datasets } = await api("/api/datasets");
    const list = $("#ds-list");
    list.innerHTML = "";
    if (!datasets.length) {
      list.appendChild(el("li", "muted", "暂无数据集，点 ＋ 新建"));
      return;
    }
    datasets.forEach((d) => {
      const name = d.replace(/^datasets\//, "");
      const li = el("li", "ds-item");
      if (name === currentDataset) li.classList.add("active");
      const row = el("div", "ds-item-row");
      row.appendChild(el("div", "run-id", name));
      // 删除按钮：页内二次确认（不用被屏蔽的 confirm()）
      const del = el("button", "btn tiny ds-del", "🗑");
      del.title = "删除数据集";
      del.onclick = (e) => {
        e.stopPropagation();
        confirmDeleteDataset(li, name);
      };
      row.appendChild(del);
      li.appendChild(row);
      li.onclick = () => openDataset(name);
      list.appendChild(li);
    });
  } catch (e) {
    setHint("加载数据集列表失败：" + e.message);
  }
}

async function openDataset(name) {
  try {
    const { text } = await api(`/api/datasets/${encodeURIComponent(name)}/text`);
    currentDataset = name;
    $("#ds-editor-title").textContent = name;
    const ta = $("#ds-text");
    ta.value = text;
    ta.disabled = false;
    $("#ds-save").disabled = false;
    await loadDatasetFiles(); // 高亮
    validateNow();
  } catch (e) {
    setHint("打开数据集失败：" + e.message);
  }
}

// 内联新建（不用 window.prompt —— VSCode 内置浏览器会屏蔽弹窗）
function toggleNewDataset(show) {
  const row = $("#ds-new-row");
  const on = show != null ? show : row.classList.contains("hidden");
  row.classList.toggle("hidden", !on);
  if (on) {
    const inp = $("#ds-new-name");
    inp.value = "";
    inp.focus();
  }
}

async function createDataset() {
  const name = $("#ds-new-name").value.trim();
  if (!name) return setHint("请输入数据集名称");
  try {
    const res = await api("/api/datasets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    toggleNewDataset(false);
    await loadDatasets(); // 同步评测下拉
    await openDataset(res.name);
    setHint(`已新建 ${res.name}`);
  } catch (e) {
    setHint("新建失败：" + e.message);
  }
}

// 页内二次确认删除（避免误删，且不依赖 window.confirm）
function confirmDeleteDataset(li, name) {
  if (li.querySelector(".ds-confirm")) return; // 已在确认态
  const bar = el("div", "ds-confirm");
  bar.appendChild(el("span", null, `删除 ${name}？`));
  const yes = el("button", "btn tiny danger", "删除");
  const no = el("button", "btn tiny", "取消");
  yes.onclick = (e) => {
    e.stopPropagation();
    deleteDataset(name);
  };
  no.onclick = (e) => {
    e.stopPropagation();
    bar.remove();
  };
  bar.appendChild(yes);
  bar.appendChild(no);
  bar.onclick = (e) => e.stopPropagation();
  li.appendChild(bar);
}

async function deleteDataset(name) {
  try {
    await api(`/api/datasets/${encodeURIComponent(name)}`, { method: "DELETE" });
    if (currentDataset === name) {
      // 关闭编辑器
      currentDataset = null;
      $("#ds-editor-title").textContent = "选择或新建一个数据集";
      const ta = $("#ds-text");
      ta.value = "";
      ta.disabled = true;
      $("#ds-save").disabled = true;
      setValidateBar("muted", "—");
    }
    await loadDatasets();      // 同步评测下拉
    await loadDatasetFiles();  // 刷新列表
    setHint(`已删除 ${name}`);
  } catch (e) {
    setHint("删除失败：" + e.message);
  }
}

function setValidateBar(state, msg) {
  const bar = $("#ds-validate");
  bar.className = "validate-bar " + state; // ok | bad | muted
  bar.textContent = msg;
}

async function validateNow() {
  const text = $("#ds-text").value;
  try {
    const r = await api("/api/datasets/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (r.ok) {
      const outline = r.cases.map((c) => `${c.id}(${c.type})`).join(" · ");
      setValidateBar("ok", `✓ ${r.cases.length} 条用例　${outline}`);
      $("#ds-save").disabled = false;
    } else {
      setValidateBar("bad", `✗ ${r.error}`);
      $("#ds-save").disabled = true;
    }
  } catch (e) {
    setValidateBar("bad", "✗ 校验请求失败：" + e.message);
  }
}

function scheduleValidate() {
  clearTimeout(validateTimer);
  validateTimer = setTimeout(validateNow, 400); // 防抖
}

async function saveDataset() {
  if (!currentDataset) return;
  const text = $("#ds-text").value;
  $("#ds-save").disabled = true;
  setHint("正在保存…");
  try {
    const r = await api(`/api/datasets/${encodeURIComponent(currentDataset)}/text`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    setHint(`已保存 ${r.name}（${r.case_count} 条）`);
    setValidateBar("ok", `✓ 已保存 · ${r.case_count} 条用例`);
    await loadDatasets(); // 评测下拉同步
  } catch (e) {
    setHint("保存失败：" + e.message);
    setValidateBar("bad", "✗ " + e.message);
  } finally {
    $("#ds-save").disabled = false;
  }
}

// 在光标处插入文本（末尾自动补换行）
function insertAtCursor(text) {
  const ta = $("#ds-text");
  if (ta.disabled) {
    setHint("请先选择或新建一个数据集");
    return;
  }
  const start = ta.selectionStart ?? ta.value.length;
  const end = ta.selectionEnd ?? ta.value.length;
  let chunk = text;
  // 确保前面有换行分隔
  if (start > 0 && ta.value[start - 1] !== "\n") chunk = "\n" + chunk;
  ta.value = ta.value.slice(0, start) + chunk + ta.value.slice(end);
  const pos = start + chunk.length;
  ta.focus();
  ta.setSelectionRange(pos, pos);
  scheduleValidate();
}

// 渲染右侧引导面板（由 meta 驱动）
function renderGuide() {
  // ① 用例模板
  const cbox = $("#guide-cases");
  cbox.innerHTML = "";
  (META.case_templates || []).forEach((t) => {
    const b = el("button", "chip", t.label);
    b.onclick = () => insertAtCursor(t.snippet);
    cbox.appendChild(b);
  });

  // ② grader 模板卡片
  const gbox = $("#guide-graders");
  gbox.innerHTML = "";
  (META.grader_kinds || []).forEach((g) => {
    const card = el("div", "gt-card");
    const head = el("div", "gt-head");
    head.appendChild(el("span", "gt-kind", g.kind));
    const ins = el("button", "btn tiny", "插入");
    ins.onclick = () => insertAtCursor(g.snippet);
    head.appendChild(ins);
    card.appendChild(head);
    card.appendChild(el("div", "gt-desc", g.desc));
    const fields = (g.fields || []).map((f) => f.name).join(" · ");
    if (fields) card.appendChild(el("div", "gt-fields", "字段：" + fields));
    gbox.appendChild(card);
  });

  // ③ toolsets 速查
  const tbox = $("#guide-toolsets");
  tbox.innerHTML = "";
  (META.toolsets || []).forEach((t) => {
    const chip = el("button", "chip", t.name);
    chip.title = t.desc;
    chip.onclick = () => insertAtCursor(t.name);
    tbox.appendChild(chip);
  });
}

// ④ 试跑 → 转断言
function insertAssertion(tc) {
  const args = tc.arguments && Object.keys(tc.arguments).length ? tc.arguments : null;
  let snip = `    - kind: tool_call\n      must_call: ${tc.name}\n      expect_success: true\n`;
  if (args) {
    const lines = Object.entries(args)
      .map(([k, v]) => `        ${k}: ${JSON.stringify(v)}`)
      .join("\n");
    snip =
      `    - kind: tool_call\n      must_call: ${tc.name}\n` +
      `      args_match:\n${lines}\n      expect_success: true\n`;
  }
  insertAtCursor(snip);
  setHint(`已插入 ${tc.name} 断言`);
}

async function tryRun() {
  const prompt = $("#try-prompt").value.trim();
  if (!prompt) return setHint("请输入试跑 prompt");
  const tsRaw = $("#try-toolsets").value.trim();
  const toolsets = tsRaw ? tsRaw.split(",").map((s) => s.trim()).filter(Boolean) : null;
  const btn = $("#try-btn");
  btn.disabled = true;
  const box = $("#try-result");
  box.innerHTML = "";
  box.appendChild(el("div", "muted", "试跑中…（调用 hermes，可能耗时）"));
  try {
    const { job_id } = await api("/api/try", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, toolsets, model: null, yolo: $("#try-yolo").checked }),
    });
    pollJob(job_id, (job, done) => {
      if (!done) return;
      btn.disabled = false;
      const ev = (job.events || []).find((e) => e.type === "try_done");
      if (job.status === "error" || !ev) {
        box.innerHTML = "";
        box.appendChild(el("div", "err", "试跑失败：" + (job.error || "未知错误")));
        return;
      }
      renderTryResult(ev.trajectory);
    });
  } catch (e) {
    btn.disabled = false;
    box.innerHTML = "";
    box.appendChild(el("div", "err", "试跑失败：" + e.message));
  }
}

function renderTryResult(t) {
  const box = $("#try-result");
  box.innerHTML = "";
  const m = t.metrics || {};
  const meta = el(
    "div",
    "try-meta",
    `耗时 ${fmtSecs(m.wall_clock)} · 工具 ${m.tool_calls ?? "-"} · API ${m.api_calls ?? "-"} · tokens ${m.total_tokens ?? "-"}`
  );
  box.appendChild(meta);
  if (t.error) box.appendChild(el("div", "err", "运行错误：" + t.error));
  const msgs = el("div", "messages compact");
  renderMessagesInto(msgs, t.messages, insertAssertion);
  box.appendChild(msgs);
}

// ============================================================================
//  预设视图：自定义 runner 参数
// ============================================================================
function presetSummary(p) {
  const parts = [];
  parts.push(p.model ? `model=${p.model}` : "默认模型");
  if (p.provider) parts.push(`provider=${p.provider}`);
  if (p.profile) parts.push(`profile=${p.profile}`);
  if (p.toolsets && p.toolsets.length) parts.push(`toolsets=[${p.toolsets.join(",")}]`);
  if (p.max_turns != null) parts.push(`max_turns=${p.max_turns}`);
  if (p.timeout != null) parts.push(`timeout=${p.timeout}s`);
  const flags = [
    p.yolo ? "yolo" : null,
    p.accept_hooks ? "accept-hooks" : null,
    p.ignore_rules ? "ignore-rules" : null,
  ].filter(Boolean);
  parts.push(`flags: ${flags.join("/") || "（全关）"}`);
  return parts.join(" · ");
}

async function loadPresets() {
  try {
    const { presets } = await api("/api/presets");
    PRESETS = presets || [];
  } catch (e) {
    PRESETS = [];
    setHint("加载预设失败：" + e.message);
  }
  renderPresetSelect();
  renderPresetList();
}

// 评测页下拉
function renderPresetSelect() {
  const sel = $("#preset-select");
  const prev = sel.value;
  sel.innerHTML = "";
  const def = el("option", null, "（默认参数）");
  def.value = "";
  sel.appendChild(def);
  PRESETS.forEach((p) => {
    const o = el("option", null, p.name);
    o.value = p.name;
    sel.appendChild(o);
  });
  sel.value = PRESETS.some((p) => p.name === prev) ? prev : "";
  updatePresetSummary();
}

function updatePresetSummary() {
  const name = $("#preset-select").value;
  const p = PRESETS.find((x) => x.name === name);
  $("#preset-summary").textContent = p ? presetSummary(p) : "使用 hermes 默认参数（yolo/accept-hooks/ignore-rules 全开）";
}

// 预设页列表
function renderPresetList() {
  const list = $("#ps-list");
  list.innerHTML = "";
  if (!PRESETS.length) {
    list.appendChild(el("li", "muted", "暂无预设，点 ＋ 新建"));
    return;
  }
  PRESETS.forEach((p) => {
    const li = el("li", "ds-item");
    if (p.name === currentPreset) li.classList.add("active");
    const row = el("div", "ds-item-row");
    row.appendChild(el("div", "run-id", p.name));
    const del = el("button", "btn tiny ds-del", "🗑");
    del.title = "删除预设";
    del.onclick = (e) => {
      e.stopPropagation();
      confirmDeletePreset(li, p.name);
    };
    row.appendChild(del);
    li.appendChild(row);
    const sub = el("div", "run-sub");
    sub.appendChild(el("span", null, p.model || "默认模型"));
    li.appendChild(sub);
    li.onclick = () => openPreset(p.name);
    list.appendChild(li);
  });
}

function openPreset(name) {
  const p = PRESETS.find((x) => x.name === name);
  if (!p) return;
  currentPreset = name;
  $("#ps-empty").classList.add("hidden");
  $("#ps-editor").classList.remove("hidden");
  $("#ps-editor-title").textContent = `预设 · ${name}`;
  $("#ps-model").value = p.model || "";
  $("#ps-provider").value = p.provider || "";
  $("#ps-profile").value = p.profile || "";
  $("#ps-toolsets").value = (p.toolsets || []).join(", ");
  $("#ps-max-turns").value = p.max_turns ?? "";
  $("#ps-timeout").value = p.timeout ?? "";
  $("#ps-yolo").checked = !!p.yolo;
  $("#ps-accept-hooks").checked = !!p.accept_hooks;
  $("#ps-ignore-rules").checked = !!p.ignore_rules;
  renderPresetList();
}

function togglePresetNew(show) {
  const row = $("#ps-new-row");
  const on = show != null ? show : row.classList.contains("hidden");
  row.classList.toggle("hidden", !on);
  if (on) {
    const inp = $("#ps-new-name");
    inp.value = "";
    inp.focus();
  }
}

async function createPreset() {
  const name = $("#ps-new-name").value.trim();
  if (!name) return setHint("请输入预设名");
  try {
    // 新建＝存一份默认参数
    await api(`/api/presets/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ timeout: 600, yolo: true, accept_hooks: true, ignore_rules: true }),
    });
    togglePresetNew(false);
    await loadPresets();
    openPreset(name);
    setHint(`已新建预设 ${name}`);
  } catch (e) {
    setHint("新建预设失败：" + e.message);
  }
}

async function savePreset() {
  if (!currentPreset) return;
  const num = (v) => (v === "" || v == null ? null : Number(v));
  const ts = $("#ps-toolsets").value.trim();
  const body = {
    model: $("#ps-model").value.trim() || null,
    provider: $("#ps-provider").value.trim() || null,
    profile: $("#ps-profile").value.trim() || null,
    toolsets: ts ? ts.split(",").map((s) => s.trim()).filter(Boolean) : null,
    max_turns: num($("#ps-max-turns").value),
    timeout: num($("#ps-timeout").value) ?? 600,
    yolo: $("#ps-yolo").checked,
    accept_hooks: $("#ps-accept-hooks").checked,
    ignore_rules: $("#ps-ignore-rules").checked,
  };
  try {
    await api(`/api/presets/${encodeURIComponent(currentPreset)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    setHint(`已保存预设 ${currentPreset}`);
    await loadPresets();
    openPreset(currentPreset);
  } catch (e) {
    setHint("保存预设失败：" + e.message);
  }
}

function confirmDeletePreset(li, name) {
  if (li.querySelector(".ds-confirm")) return;
  const bar = el("div", "ds-confirm");
  bar.appendChild(el("span", null, `删除预设 ${name}？`));
  const yes = el("button", "btn tiny danger", "删除");
  const no = el("button", "btn tiny", "取消");
  yes.onclick = (e) => {
    e.stopPropagation();
    deletePreset(name);
  };
  no.onclick = (e) => {
    e.stopPropagation();
    bar.remove();
  };
  bar.appendChild(yes);
  bar.appendChild(no);
  bar.onclick = (e) => e.stopPropagation();
  li.appendChild(bar);
}

async function deletePreset(name) {
  try {
    await api(`/api/presets/${encodeURIComponent(name)}`, { method: "DELETE" });
    if (currentPreset === name) {
      currentPreset = null;
      $("#ps-editor").classList.add("hidden");
      $("#ps-empty").classList.remove("hidden");
    }
    await loadPresets();
    setHint(`已删除预设 ${name}`);
  } catch (e) {
    setHint("删除预设失败：" + e.message);
  }
}

// ---- 绑定事件 --------------------------------------------------------------
$("#run-btn").onclick = startRun;
$("#stop-btn").onclick = cancelActiveJob;
$("#regrade-btn").onclick = regrade;
$("#refresh-runs").onclick = loadRuns;
$("#drawer-close").onclick = closeDrawer;
$("#drawer-overlay").onclick = closeDrawer;
document.addEventListener("keydown", (e) => e.key === "Escape" && closeDrawer());

$("#ds-new").onclick = () => toggleNewDataset();
$("#ds-new-ok").onclick = createDataset;
$("#ds-new-cancel").onclick = () => toggleNewDataset(false);
$("#ds-new-name").addEventListener("keydown", (e) => {
  if (e.key === "Enter") createDataset();
  else if (e.key === "Escape") toggleNewDataset(false);
});
$("#ds-save").onclick = saveDataset;
$("#ds-text").addEventListener("input", scheduleValidate);
$("#try-btn").onclick = tryRun;

$("#preset-select").onchange = updatePresetSummary;
$("#ps-new").onclick = () => togglePresetNew();
$("#ps-new-ok").onclick = createPreset;
$("#ps-new-cancel").onclick = () => togglePresetNew(false);
$("#ps-new-name").addEventListener("keydown", (e) => {
  if (e.key === "Enter") createPreset();
  else if (e.key === "Escape") togglePresetNew(false);
});
$("#ps-save").onclick = savePreset;

// ---- 启动 ------------------------------------------------------------------
loadMeta();
loadDatasets();
loadRuns();
loadDatasetFiles();
loadPresets();
