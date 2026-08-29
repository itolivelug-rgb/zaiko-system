// 貸出・返却スキャン画面の共通処理
// 呼び出し側で initScanPage({...}) を実行する

function initScanPage(config) {
    const allItems = config.items;
    const doneCodes = new Set(config.doneCodes);
    const blockedCodes = new Set(config.blockedCodes || []);
    const labels = config.labels;

    const pending = new Map();
    const scanned = new Map();
    allItems.forEach(function (i) {
        if (doneCodes.has(i.code)) {
            scanned.set(i.code, i);
        } else {
            pending.set(i.code, i);
        }
    });

    const input = document.getElementById("scan-input");
    const msg = document.getElementById("scan-msg");
    const pendingList = document.getElementById("pending-list");
    const scannedList = document.getElementById("scanned-list");
    const pendingCnt = document.getElementById("pending-cnt");
    const scannedCnt = document.getElementById("scanned-cnt");
    const submitBtn = document.getElementById("submit-btn");
    const submitNote = document.getElementById("submit-note");
    const hiddenInputs = document.getElementById("hidden-inputs");
    const form = document.getElementById("scan-form");

    // --- 音（Web Audio APIで生成） ---
    let audioCtx = null;
    function beep(ok) {
        try {
            if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            const now = audioCtx.currentTime;

            if (ok) {
                const osc = audioCtx.createOscillator();
                const gain = audioCtx.createGain();
                osc.connect(gain);
                gain.connect(audioCtx.destination);
                osc.type = "square";
                osc.frequency.value = 1200;
                gain.gain.setValueAtTime(0.36, now);
                gain.gain.exponentialRampToValueAtTime(0.001, now + 0.09);
                osc.start(now);
                osc.stop(now + 0.09);
            } else {
                [0, 0.09, 0.18].forEach(function (t) {
                    const osc = audioCtx.createOscillator();
                    const gain = audioCtx.createGain();
                    osc.connect(gain);
                    gain.connect(audioCtx.destination);
                    osc.type = "square";
                    osc.frequency.value = 1000;
                    gain.gain.setValueAtTime(0.4, now + t);
                    gain.gain.setValueAtTime(0.4, now + t + 0.055);
                    gain.gain.exponentialRampToValueAtTime(0.001, now + t + 0.065);
                    osc.start(now + t);
                    osc.stop(now + t + 0.07);
                });
            }
        } catch (e) {}
    }

    // --- 描画 ---
    function renderItem(item, flash) {
        const div = document.createElement("div");
        div.className = "item" + (flash ? " flash" : "") + (doneCodes.has(item.code) ? " done" : "");
        div.innerHTML = '<span class="code"></span><span class="area"></span><span class="model"></span>';
        div.querySelector(".code").textContent = item.code;
        div.querySelector(".area").textContent = item.area;
        div.querySelector(".model").textContent = item.model + (item.name ? "　" + item.name : "");
        return div;
    }

    function render(flashCode) {
        pendingList.innerHTML = "";
        scannedList.innerHTML = "";

        if (pending.size === 0) {
            pendingList.innerHTML = '<div class="pane-empty">' + labels.allDone + "</div>";
        } else {
            pending.forEach(function (item) { pendingList.appendChild(renderItem(item, false)); });
        }

        if (scanned.size === 0) {
            scannedList.innerHTML = '<div class="pane-empty">まだ読み込んでいません。</div>';
        } else {
            scanned.forEach(function (item) {
                scannedList.appendChild(renderItem(item, item.code === flashCode));
            });
        }

        pendingCnt.textContent = pending.size + " 点";
        scannedCnt.textContent = scanned.size + " 点";

        hiddenInputs.innerHTML = "";
        let newCount = 0;
        scanned.forEach(function (item) {
            if (doneCodes.has(item.code)) return;
            const h = document.createElement("input");
            h.type = "hidden";
            h.name = "scanned_ids";
            h.value = item.id;
            hiddenInputs.appendChild(h);
            newCount++;
        });

        submitBtn.disabled = newCount === 0;
        submitNote.textContent = newCount === 0
            ? "読み込んだ機材はありません。"
            : newCount + " 点を" + labels.action + "します。";
    }

    // --- コードを受け取ったときの処理 ---
    function handleCode(code) {
        if (blockedCodes.has(code)) {
            beep(false);
            msg.className = "scan-msg ng";
            msg.textContent = code + " " + labels.blockedShort;
            alert(code + labels.blockedAlert);
            return;
        }
        if (scanned.has(code)) {
            beep(false);
            msg.className = "scan-msg ng";
            msg.textContent = code + " " + labels.alreadyDone;
            return;
        }
        if (!pending.has(code)) {
            beep(false);
            msg.className = "scan-msg ng";
            msg.textContent = code + " はこの案件に登録されていません。";
            return;
        }
        const item = pending.get(code);
        pending.delete(code);
        scanned.set(code, item);
        beep(true);
        msg.className = "scan-msg ok";
        msg.textContent = code + " " + item.model;
        render(code);
    }

    // --- 入力 ---
    input.addEventListener("input", function () {
        const v = input.value.trim();
        if (v.length >= 10) {
            input.value = "";
            handleCode(v.slice(0, 10));
        }
    });

    input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
            e.preventDefault();
            const v = input.value.trim();
            input.value = "";
            if (v) handleCode(v);
        }
    });

    // 入力欄からフォーカスが外れても戻す
    document.addEventListener("click", function (e) {
        if (e.target.tagName !== "BUTTON" && e.target.tagName !== "A") {
            input.focus();
        }
    });

    // --- 離脱時の警告 ---
    let submitting = false;
    window.addEventListener("beforeunload", function (e) {
        if (hiddenInputs.children.length > 0 && !submitting) {
            e.preventDefault();
            e.returnValue = "";
        }
    });
    form.addEventListener("submit", function () {
        submitting = true;
    });

    render(null);
}