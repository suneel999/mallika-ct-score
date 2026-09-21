const form = document.getElementById("report-form");
const autoTotal = document.getElementById("auto-total");
const totalInput = document.getElementById("total");
const warningEl = document.getElementById("total-warning");
const statusEl = document.getElementById("status");
const riskInput = document.getElementById("risk");
const previewFrame = document.getElementById("preview-frame");
const previewEmpty = document.getElementById("preview-empty");
const downloadLink = document.getElementById("download-link");
const imageInput = document.getElementById("image");
const imagePreview = document.getElementById("image-preview");
const imagePreviewEl = document.getElementById("image-preview-el");

function vesselSum() {
  const names = ["lm", "lad", "lcx", "rca"];
  return names.reduce((sum, name) => {
    const value = parseFloat(form.elements[name].value);
    return sum + (Number.isFinite(value) ? value : 0);
  }, 0);
}

function formatScore(value) {
  return (Number.isFinite(value) ? value : 0).toFixed(2);
}

function riskFromScore(score) {
  if (score <= 0) return "Very Low";
  if (score <= 10) return "Low";
  if (score <= 100) return "Moderate";
  if (score <= 400) return "Moderate to High";
  return "High";
}

function updateTotal() {
  const sum = vesselSum();
  if (autoTotal.checked) {
    totalInput.value = formatScore(sum);
    totalInput.readOnly = true;
    warningEl.hidden = true;
    return;
  }
  totalInput.readOnly = false;
  const manual = parseFloat(totalInput.value);
  if (totalInput.value !== "" && Number.isFinite(manual) && Math.abs(manual - sum) > 0.009) {
    warningEl.hidden = false;
    warningEl.textContent = `Warning: total (${formatScore(manual)}) does not match LM+LAD+LCX+RCA (${formatScore(sum)}).`;
  } else {
    warningEl.hidden = true;
  }
}

function updateRisk() {
  if (riskInput.dataset.manual === "true") return;
  const score = parseFloat(form.elements.score.value);
  if (!Number.isFinite(score)) return;
  riskInput.value = riskFromScore(score);
}

function showStatus(message, kind) {
  statusEl.hidden = !message;
  statusEl.className = `status ${kind || "info"}`;
  statusEl.textContent = message || "";
}

form.addEventListener("input", (event) => {
  if (["lm", "lad", "lcx", "rca", "total"].includes(event.target.name) || event.target === autoTotal) {
    updateTotal();
  }
  if (event.target.name === "score") {
    riskInput.dataset.manual = "false";
    updateRisk();
  }
});

riskInput.addEventListener("input", () => {
  riskInput.dataset.manual = "true";
});

autoTotal.addEventListener("change", updateTotal);
document.getElementById("calc-total").addEventListener("click", () => {
  autoTotal.checked = true;
  updateTotal();
});

imageInput.addEventListener("change", () => {
  const file = imageInput.files[0];
  if (!file) {
    imagePreview.hidden = true;
    return;
  }
  imagePreviewEl.src = URL.createObjectURL(file);
  imagePreview.hidden = false;
});

document.getElementById("load-sample").addEventListener("click", () => {
  const values = {
    name: "Pandajah",
    id: "MH120010",
    age: "45",
    ref: "Dr. J Mahesh Babu MD DM",
    s_date: "2026-09-08",
    r_date: "2026-09-08",
    score: "0",
    start: "0",
    end: "10",
    lm: "0",
    lad: "0",
    lcx: "0",
    rca: "0",
    total: "0.00",
    reportedby: "Dr. Kanav Kansal",
    qual: "DNB Radiodiagnosis",
    des: "Consultant Radiologist",
    risk: "Very Low",
  };
  Object.entries(values).forEach(([key, value]) => {
    if (form.elements[key]) form.elements[key].value = value;
  });
  autoTotal.checked = true;
  riskInput.dataset.manual = "false";
  updateTotal();
  updateRisk();
});

async function generate(downloadAfter) {
  showStatus("Generating the report from the original template…", "info");
  const previewBtn = document.getElementById("preview-btn");
  const generateBtn = document.getElementById("generate-btn");
  previewBtn.disabled = true;
  generateBtn.disabled = true;

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      body: new FormData(form),
    });
    const payload = await response.json();
    if (!payload.ok) {
      showStatus(payload.error || "Could not generate the report.", "error");
      return;
    }

    previewEmpty.hidden = true;
    previewFrame.hidden = false;
    previewFrame.src = `${payload.preview_url}#view=FitH`;
    downloadLink.hidden = false;
    downloadLink.href = payload.download_url;
    downloadLink.download = payload.filename;
    downloadLink.textContent = `Download ${payload.filename}`;

    let message = "Report generated from the original template.";
    if (payload.warning) message += ` ${payload.warning}`;
    showStatus(message, payload.warning ? "info" : "ok");

    if (downloadAfter) {
      window.location.href = payload.download_url;
    }
  } catch (error) {
    showStatus(error.message || "Network error while generating the PDF.", "error");
  } finally {
    previewBtn.disabled = false;
    generateBtn.disabled = false;
  }
}

document.getElementById("preview-btn").addEventListener("click", () => {
  if (!form.reportValidity()) return;
  generate(false);
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  generate(true);
});

updateTotal();
updateRisk();
