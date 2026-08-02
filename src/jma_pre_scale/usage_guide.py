"""FX 利用手順パレットと同系デザインのドラッグ可能ガイド。"""
from __future__ import annotations

USAGE_GUIDE_CSS = """
.usage-guide-panel {
  --ug-accent: #6366f1;
  --ug-accent-soft: rgba(99, 102, 241, 0.12);
  --ug-panel: #ffffff;
  --ug-text: #1e293b;
  --ug-muted: #64748b;
  --ug-border: rgba(99, 102, 241, 0.2);
  position: fixed;
  z-index: 1200;
  width: min(420px, calc(100vw - 16px));
  border-radius: 12px;
  border: 1px solid var(--ug-border);
  background: var(--ug-panel);
  backdrop-filter: blur(12px);
  box-shadow: 0 12px 40px rgba(49, 46, 129, 0.18);
  overflow: hidden;
  user-select: none;
  touch-action: none;
  color: var(--ug-text);
  left: 24px;
  top: 72px;
}
.usage-guide-panel::before {
  content: "";
  position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, var(--ug-accent), #7c3aed); z-index: 1;
}
.usage-guide-panel.is-dragging {
  border-color: rgba(99, 102, 241, 0.45);
  box-shadow: 0 16px 48px rgba(49, 46, 129, 0.28);
  cursor: grabbing;
}
.usage-guide-panel.is-collapsed .usage-guide-body { display: none; }
.usage-guide-header {
  display: flex; align-items: center; justify-content: space-between; gap: .5rem;
  padding: .75rem .85rem; background: rgba(255,255,255,.92);
  border-bottom: 1px solid var(--ug-border); cursor: grab;
}
.usage-guide-panel.is-dragging .usage-guide-header { cursor: grabbing; }
.usage-guide-header-text { display: flex; align-items: center; gap: .5rem; min-width: 0; flex: 1; }
.usage-guide-header-titles { display: flex; flex-direction: column; gap: .1rem; min-width: 0; }
.usage-guide-header-text strong {
  display: block; font-size: .95rem; font-weight: 700; letter-spacing: -.03em; color: var(--ug-text);
}
.usage-guide-header-text strong::before {
  content: ""; display: inline-block; width: 4px; height: 1.05em; border-radius: 4px;
  background: linear-gradient(180deg, var(--ug-accent), #7c3aed); margin-right: .45rem; vertical-align: -.15em;
}
.usage-guide-header-sub {
  font-size: .62rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--ug-muted);
}
.usage-guide-drag-icon { color: var(--ug-muted); font-size: .82rem; }
.usage-guide-drag-hint {
  font-size: .72rem; font-weight: 600; letter-spacing: .04em; color: var(--ug-accent); white-space: nowrap;
}
.usage-guide-toggle {
  flex-shrink: 0; width: 2rem; height: 2rem; border-radius: 999px; border: 1px solid var(--ug-border);
  background: #fff; color: var(--ug-accent); font-size: .82rem; cursor: pointer;
}
.usage-guide-toggle:hover { background: var(--ug-accent-soft); }
.usage-guide-body {
  max-height: min(85vh, 720px); overflow-y: auto; padding: .9rem .95rem 1rem;
  background: linear-gradient(180deg, rgba(255,255,255,.5) 0%, rgba(245,243,255,.4) 100%);
  user-select: text;
}
.usage-guide-hero {
  margin-bottom: .75rem; padding: .85rem .9rem; border-radius: 12px;
  border: 1px solid rgba(99,102,241,.22);
  background:
    radial-gradient(ellipse at top right, rgba(124,58,237,.12), transparent 55%),
    linear-gradient(135deg, rgba(255,255,255,.95), rgba(237,233,254,.88));
}
.usage-guide-hero-kicker {
  margin: 0 0 .35rem; font-size: .62rem; font-weight: 800; letter-spacing: .12em;
  text-transform: uppercase; color: #7c3aed;
}
.usage-guide-hero-title {
  margin: 0 0 .35rem; font-size: .92rem; font-weight: 800; line-height: 1.35; color: #1e1b4b;
}
.usage-guide-hero-lead { margin: 0 0 .55rem; font-size: .78rem; line-height: 1.55; color: var(--ug-text); }
.usage-guide-stack { display: flex; flex-wrap: wrap; gap: .35rem; }
.usage-guide-stack-pill {
  padding: .18rem .45rem; border-radius: 999px; border: 1px solid rgba(99,102,241,.18);
  background: rgba(255,255,255,.85); font-size: .62rem; font-weight: 700; color: #4338ca;
}
.usage-guide-diagram { margin: 0 0 .75rem; }
.usage-guide-diagram figcaption {
  margin: 0 0 .35rem; font-size: .68rem; font-weight: 700; letter-spacing: .06em;
  text-transform: uppercase; color: var(--ug-muted);
}
.usage-guide-diagram pre {
  margin: 0; padding: .65rem .7rem; border-radius: 10px; border: 1px solid rgba(15,23,42,.12);
  background: #0f172a; color: #c7d2fe; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: .58rem; line-height: 1.45; overflow-x: auto; white-space: pre;
}
.usage-guide-featured {
  margin-bottom: .75rem; padding: .75rem .8rem; border-radius: 10px;
  border: 1px solid rgba(99,102,241,.35);
  background: linear-gradient(135deg, rgba(237,233,254,.95), rgba(224,231,255,.85));
}
.usage-guide-featured--architecture {
  border-color: rgba(124,58,237,.4);
  background: linear-gradient(135deg, rgba(237,233,254,.98), rgba(196,181,253,.35));
}
.usage-guide-featured--safety {
  border-color: rgba(14,165,233,.4);
  background: linear-gradient(135deg, rgba(224,242,254,.98), rgba(186,230,253,.4));
}
.usage-guide-featured--purpose {
  border-color: rgba(234,88,12,.4);
  background: linear-gradient(135deg, rgba(255,247,237,.98), rgba(254,215,170,.35));
}
.usage-guide-featured-head { display: flex; align-items: center; gap: .5rem; margin-bottom: .35rem; }
.usage-guide-featured-head strong { font-size: .84rem; color: #312e81; }
.usage-guide-featured-badge {
  display: inline-flex; align-items: center; padding: .15rem .5rem; border-radius: 999px;
  background: #7c3aed; color: #fff; font-size: .58rem; font-weight: 800; letter-spacing: .06em;
}
.usage-guide-featured--safety .usage-guide-featured-badge { background: #0284c7; }
.usage-guide-featured--purpose .usage-guide-featured-badge { background: #ea580c; }
.usage-guide-featured--purpose .usage-guide-featured-head strong { color: #9a3412; }
.usage-guide-featured p { margin: 0 0 .4rem; font-size: .76rem; line-height: 1.55; color: var(--ug-text); }
.usage-guide-items { margin: .35rem 0 0; padding-left: 1.05rem; }
.usage-guide-items li { margin: .25rem 0; font-size: .72rem; line-height: 1.55; color: #334155; }
.usage-guide-scroll-hint {
  margin: 0 0 .55rem; font-size: .68rem; font-weight: 700; letter-spacing: .04em; color: #7c3aed;
}
.usage-guide-workflow-title {
  margin: 0 0 .45rem; font-size: .78rem; font-weight: 800; letter-spacing: .04em; color: #312e81;
}
.usage-guide-section { margin-bottom: .7rem; }
.usage-guide-section-label {
  margin: 0 0 .35rem; font-size: .62rem; font-weight: 800; letter-spacing: .1em;
  text-transform: uppercase; color: var(--ug-muted);
}
.usage-guide-steps { margin: 0; padding: 0; list-style: none; counter-reset: ug-step; }
.usage-guide-steps > li {
  counter-increment: ug-step; margin: 0 0 .45rem; padding: .65rem .7rem .65rem 2.6rem;
  border-radius: 10px; border: 1px solid rgba(99,102,241,.2); background: rgba(255,255,255,.9);
  position: relative;
}
.usage-guide-steps > li::before {
  content: counter(ug-step, decimal-leading-zero);
  position: absolute; left: .55rem; top: .65rem; width: 1.55rem; height: 1.55rem;
  border-radius: 8px; background: #7c3aed; color: #fff; font-size: .62rem; font-weight: 800;
  display: grid; place-items: center;
}
.usage-guide-steps strong { display: block; margin-bottom: .2rem; font-size: .8rem; color: #1e1b4b; }
.usage-guide-steps p { margin: 0; font-size: .72rem; line-height: 1.55; color: #475569; }
.usage-guide-footer {
  margin: .75rem 0 0; padding-top: .65rem; border-top: 1px solid rgba(99,102,241,.15);
  font-size: .68rem; line-height: 1.5; color: var(--ug-muted);
}
@media (max-width: 720px) {
  .usage-guide-panel { left: 8px !important; right: 8px; width: auto; top: auto !important; bottom: 8px; }
  .usage-guide-drag-hint { display: none; }
  .usage-guide-body { max-height: min(70vh, 560px); }
}
"""

USAGE_GUIDE_SCRIPT = """
<script>
(function () {
  const KEY = 'jma-pre-scale-usage-guide-v1';
  const panel = document.getElementById('usage-guide');
  const header = document.getElementById('usage-guide-header');
  const toggle = document.getElementById('usage-guide-toggle');
  if (!panel || !header || !toggle) return;

  const defaultPos = () => {
    const w = Math.min(420, window.innerWidth - 16);
    return { x: Math.max(16, window.innerWidth - w - 24), y: Math.max(72, window.innerHeight - 560) };
  };

  let state = { x: 24, y: 72, expanded: true };
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (saved && typeof saved.x === 'number') state = { ...state, ...saved };
  } catch (_) {}
  if (window.innerWidth < 720) state.expanded = false;

  const apply = () => {
    panel.style.left = state.x + 'px';
    panel.style.top = state.y + 'px';
    panel.classList.toggle('is-collapsed', !state.expanded);
    panel.classList.toggle('is-expanded', state.expanded);
    toggle.textContent = state.expanded ? '▼' : '▲';
    toggle.setAttribute('aria-label', state.expanded ? '閉じる' : '開く');
    localStorage.setItem(KEY, JSON.stringify(state));
  };
  apply();

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    state.expanded = !state.expanded;
    apply();
  });

  let drag = null;
  header.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.usage-guide-toggle')) return;
    if (window.innerWidth < 720) return;
    drag = { pid: e.pointerId, sx: e.clientX, sy: e.clientY, ox: state.x, oy: state.y };
    header.setPointerCapture(e.pointerId);
    panel.classList.add('is-dragging');
  });
  header.addEventListener('pointermove', (e) => {
    if (!drag || e.pointerId !== drag.pid) return;
    const w = panel.offsetWidth, h = panel.offsetHeight;
    state.x = Math.min(Math.max(8, drag.ox + (e.clientX - drag.sx)), Math.max(8, window.innerWidth - w - 8));
    state.y = Math.min(Math.max(8, drag.oy + (e.clientY - drag.sy)), Math.max(8, window.innerHeight - h - 8));
    apply();
  });
  const endDrag = (e) => {
    if (!drag || e.pointerId !== drag.pid) return;
    drag = null;
    panel.classList.remove('is-dragging');
  };
  header.addEventListener('pointerup', endDrag);
  header.addEventListener('pointercancel', endDrag);
})();
</script>
"""


def usage_guide_html() -> str:
    topology = """Browser / Ops
  └─ Railway Web (uvicorn :$PORT)
       ├─ GET  /health
       ├─ GET  /api/v1/status
       └─ POST /api/v1/poll   ← dry-run 判定

JMA Atom (extra / eqvol)
  └─ Poller Lambda (1 min)
       ├─ DynamoDB 状態・重複排除・ロック・監査
       └─ Step Functions (SCALE_OUT / SCALE_IN 時のみ)
            Plan → (LEVEL_3 承認) → Apply
            → HealthCheck → Notify
                 ├─ Aurora Serverless v2 最小ACU
                 ├─ ECS App Auto Scaling MinCapacity
                 └─ ECS desiredCount
                      └─ 既存アプリ (無改修)"""

    return f"""
<aside id="usage-guide" class="usage-guide-panel is-expanded" role="dialog" aria-label="利用手順">
  <header id="usage-guide-header" class="usage-guide-header">
    <div class="usage-guide-header-text">
      <span class="usage-guide-drag-icon" aria-hidden="true">☰</span>
      <div class="usage-guide-header-titles">
        <strong>利用手順</strong>
        <span class="usage-guide-header-sub">Architecture &amp; Ops</span>
      </div>
      <span class="usage-guide-drag-hint">ドラッグで移動</span>
    </div>
    <button id="usage-guide-toggle" class="usage-guide-toggle" type="button" aria-label="閉じる">▼</button>
  </header>
  <div class="usage-guide-body">
    <section class="usage-guide-hero">
      <p class="usage-guide-hero-kicker">JMA Disaster Pre-Scale</p>
      <h2 class="usage-guide-hero-title">気象庁防災情報XML 事前スケール</h2>
      <p class="usage-guide-hero-lead">
        平時はほぼ無負荷、災害発生時は数万IDが一斉アクセスする特性向けに、
        <b>負荷検知型オートスケールではなく</b>、気象庁防災情報XMLをフックとして
        アクセス集中前にインフラを先行自動拡張（事前スケールアウト）する制御基盤です。
        Railway 上の画面は監視・dry-run 用。実適用は AWS (Terraform / Lambda / Step Functions) です。
      </p>
      <div class="usage-guide-stack" aria-label="Tech stack">
        <span class="usage-guide-stack-pill">Python 3.11+</span>
        <span class="usage-guide-stack-pill">FastAPI • uvicorn</span>
        <span class="usage-guide-stack-pill">JMA Atom XML フック</span>
        <span class="usage-guide-stack-pill">事前スケールアウト</span>
        <span class="usage-guide-stack-pill">非・負荷検知型</span>
        <span class="usage-guide-stack-pill">Lambda • Step Functions</span>
        <span class="usage-guide-stack-pill">DynamoDB</span>
        <span class="usage-guide-stack-pill">ECS Fargate</span>
        <span class="usage-guide-stack-pill">Aurora Serverless v2</span>
        <span class="usage-guide-stack-pill">Terraform</span>
        <span class="usage-guide-stack-pill">dry_run 既定 ON</span>
      </div>
    </section>

    <section class="usage-guide-featured usage-guide-featured--purpose" aria-label="設計意図">
      <div class="usage-guide-featured-head">
        <span class="usage-guide-featured-badge">WHY PRE-SCALE</span>
        <strong>なぜ負荷検知型では足りないか</strong>
      </div>
      <p>
        本システムは平時の利用がほぼなく、災害発生時に数万IDが短時間で集中します。
        CPU/RPS などの負荷検知型オートスケールはスパイク検知後に起動するため、
        コンテナ起動・DB 容量確保が完了する前にリクエストが溢れます。
        そのため気象庁 Atom（extra.xml / eqvol.xml）を外部監視し、
        警報・注意報の発表を契機に <b>アクセス到達前</b> へ ECS / Aurora を絶対容量で先行拡張します。
      </p>
      <ul class="usage-guide-items">
        <li>判定トリガーは負荷メトリクスではなく JMA XML（注意報・警報・震度・津波など）</li>
        <li>既存アプリのターゲット追跡オートスケールがあっても、本制御が MinCapacity / desired を先に上げる</li>
        <li>EventBridge 毎分 Poller → 解析・重複排除 → SCALE_OUT 時のみ Step Functions</li>
        <li>現状は安全ひな型（dry_run 既定）。本番適用は Terraform 設定後に dry_run 解除</li>
      </ul>
    </section>

    <section class="usage-guide-featured usage-guide-featured--architecture" aria-label="アーキテクチャ">
      <div class="usage-guide-featured-head">
        <span class="usage-guide-featured-badge">ARCHITECTURE</span>
        <strong>Poller → Step Functions → 容量制御</strong>
      </div>
      <p>
        EventBridge が毎分 Poller を起動。取得・検証・重複排除・判定のあと、
        拡張/縮小が必要なときだけ Step Functions を起動します。
        適用順は <b>Aurora 最小ACU → ECS MinCapacity → ECS DesiredCount</b> です。
        負荷メトリクスは判定に使いません（事前拡張専用パス）。
      </p>
      <ul class="usage-guide-items">
        <li>既存アプリのコード・DBスキーマ・業務ロジックは一切変更しない</li>
        <li>LEVEL_0→1→2→3：注意報 / 警報 / 特別警報・高震度・津波警報などで段階拡張（容量は実測で置換）</li>
        <li>LEVEL_3（特別警報等）は人手承認。放置すると10分後に LEVEL_2 へフォールバック</li>
        <li>縮小時は拡張と逆順。自動縮小は既定オフ（手動で平時へ戻す）</li>
      </ul>
    </section>

    <section class="usage-guide-featured usage-guide-featured--safety" aria-label="安全設計">
      <div class="usage-guide-featured-head">
        <span class="usage-guide-featured-badge">SAFETY</span>
        <strong>安全側の原則</strong>
      </div>
      <p>判断に迷ったら拡張側・HOLD・dry_run 維持に倒します。</p>
      <ul class="usage-guide-items">
        <li>フィード全滅時は HOLD（容量変更しない）</li>
        <li>突発災害向け baseline_reserve_tasks を平時から確保</li>
        <li>分散ロックで多重 Poller 実行を抑止</li>
        <li>初期導入では必ず <code>dry_run: true</code></li>
      </ul>
    </section>

    <figure class="usage-guide-diagram" aria-label="Service topology">
      <figcaption>Service topology</figcaption>
      <pre>{topology}</pre>
    </figure>

    <p class="usage-guide-scroll-hint">↓ 詳細利用手順・日常運用・警報時対応は下へ</p>
    <h3 class="usage-guide-workflow-title">詳細利用手順</h3>

    <div class="usage-guide-section">
      <p class="usage-guide-section-label">0. 前提（必ず理解）</p>
      <ol class="usage-guide-steps">
        <li>
          <strong>アクセス特性</strong>
          <p>平時はほぼ無利用。災害発生時は数万IDが一斉アクセスします。通常の負荷検知型オートスケールではスパイクに間に合いません。</p>
        </li>
        <li>
          <strong>本システムの役割</strong>
          <p>気象庁防災情報XMLをフックに、アクセス集中前へインフラを先行自動拡張します。判定に CPU/RPS は使いません。</p>
        </li>
        <li>
          <strong>実装状態</strong>
          <p>コード上は事前スケールパス一式（Poller / 判定 / Step Functions / ECS・Aurora 適用）が実装済み。既定は dry_run ひな型で、本番適用は Terraform 設定と dry_run 解除が必要です。Railway はこの監視・dry-run 面のみです。</p>
        </li>
      </ol>
    </div>

    <div class="usage-guide-section">
      <p class="usage-guide-section-label">A. Railway ops 面（この画面）</p>
      <ol class="usage-guide-steps">
        <li>
          <strong>稼働確認</strong>
          <p><code>GET /health</code> が ok、<code>GET /api/v1/status</code> で dry_run / 現在レベル / feed URL を確認します。</p>
        </li>
        <li>
          <strong>dry-run ポーリング</strong>
          <p>「dry-run ポーリング実行」または <code>POST /api/v1/poll</code> で、気象庁フィード取得〜判定までを1回実行します。実 ECS/Aurora は変更しません。</p>
        </li>
        <li>
          <strong>結果の読み方</strong>
          <p><code>decision.action</code>（HOLD / SCALE_OUT / SCALE_IN）、<code>level</code>、<code>reason</code>、<code>fetch_errors</code> を確認。would_scale=true でも dry_run 中は計画ログのみです。</p>
        </li>
      </ol>
    </div>

    <div class="usage-guide-section">
      <p class="usage-guide-section-label">B. 初期導入（AWS）</p>
      <ol class="usage-guide-steps">
        <li>
          <strong>設定を用意</strong>
          <p><code>config/config.yaml</code> と <code>terraform/terraform.tfvars</code> を編集。<code>dry_run = true</code> のまま ECS / Aurora / SNS 名を埋めます。</p>
        </li>
        <li>
          <strong>テスト</strong>
          <p><code>pytest</code> または <code>python tests/run_tests.py</code>。AWS 接続なしで判定・安全設計を検証できます。</p>
        </li>
        <li>
          <strong>Terraform 適用</strong>
          <p><code>cd terraform && terraform init && terraform plan && terraform apply</code>。負荷試験と運用承認が終わるまで dry_run を外さないでください。</p>
        </li>
        <li>
          <strong>レベル別容量は実測で置換</strong>
          <p>同梱の 2/5/15/40 はプレースホルダです。RPS・起動時間・接続上限の実測後に差し替えます。</p>
        </li>
      </ol>
    </div>

    <div class="usage-guide-section">
      <p class="usage-guide-section-label">C. 日常運用</p>
      <ol class="usage-guide-steps">
        <li>
          <strong>毎日の確認</strong>
          <p>CloudWatch で Poller 起動/エラー、現在レベル（平時は 0）、アラーム OK を確認します。</p>
        </li>
        <li>
          <strong>状態照会</strong>
          <p>Override Lambda に <code>{{"op":"status"}}</code> を invoke。current_level / automation_disabled を見ます。</p>
        </li>
        <li>
          <strong>監査ログ</strong>
          <p>CloudWatch Logs の <code>jma_pre_scale_audit</code> と DynamoDB 監査レコードで判定履歴を追跡します。</p>
        </li>
      </ol>
    </div>

    <div class="usage-guide-section">
      <p class="usage-guide-section-label">D. 警報・特別警報時</p>
      <ol class="usage-guide-steps">
        <li>
          <strong>通常の警報（自動）</strong>
          <p>SCALE_OUT 通知を受けたら ECS Desired / Running の追随を確認。10分経っても増えない場合は障害手順へ。</p>
        </li>
        <li>
          <strong>LEVEL_3 承認</strong>
          <p>承認メールの reason / plan を確認し、<code>send-task-success</code> で承認。迷ったら放置（10分後に LEVEL_2）。</p>
        </li>
        <li>
          <strong>手動介入</strong>
          <p>force_level / clear_force / disable_automation / enable_automation。operator 名必須。強制レベル中は電文判定より優先されます。</p>
        </li>
      </ol>
    </div>

    <div class="usage-guide-section">
      <p class="usage-guide-section-label">E. 本番切替チェックリスト</p>
      <ol class="usage-guide-steps">
        <li>
          <strong>dry_run 解除前</strong>
          <p>負荷試験完了、レベル別容量の実測反映、承認トピック到達確認、ロールバック手順の読み合わせを済ませます。</p>
        </li>
        <li>
          <strong>切替</strong>
          <p>設定の <code>dry_run: false</code>（または DRY_RUN=false）を反映。直後はダッシュボードを連続監視します。</p>
        </li>
        <li>
          <strong>異常時</strong>
          <p>まず disable_automation。必要なら手動で平時容量へ。詳細は docs/03_incident_response.md。</p>
        </li>
      </ol>
    </div>

    <p class="usage-guide-footer">
      ▼▲ で開閉 · PC はヘッダーをドラッグして移動 · 表示位置はブラウザに自動保存 ·
      詳細ドキュメント: docs/01_design.md / 02_runbook.md / 03_incident_response.md
    </p>
  </div>
</aside>
{USAGE_GUIDE_SCRIPT}
"""
