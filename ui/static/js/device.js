// set Default start and end time for breakglass account request pop up
document.body.addEventListener("htmx:afterSwap", function (evt) {
  // Only react when #modal is updated
  if (evt.target.id !== "modal") return;

  const startInput = document.getElementById("start-time");
  const endInput = document.getElementById("end-time");
  if (!startInput || !endInput) return;

  const now = new Date();
  const pad = n => n.toString().padStart(2, "0");

  const yyyy = now.getFullYear();
  const mm = pad(now.getMonth() + 1);
  const dd = pad(now.getDate());
  const hh = pad(now.getHours());
  const mi = pad(now.getMinutes());

  const start = `${yyyy}-${mm}-${dd}T${hh}:${mi}`;

  const endDate = new Date(now.getTime() + 30 * 60000);
  const ehh = pad(endDate.getHours());
  const emi = pad(endDate.getMinutes());
  const end = `${yyyy}-${mm}-${dd}T${ehh}:${emi}`;

  startInput.value = start;
  endInput.value = end;
});

function showCopyToast(message) {
  const toast = document.getElementById("copy-toast");
  if (!toast) return;

  toast.textContent = message;
  toast.style.visibility = "visible";
  toast.style.opacity = "1";

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.visibility = "hidden";
  }, 3000);
}


// Show toast in corner
// function showCopyToast(message) {
//   // Remove existing toast if present
//   const existing = document.getElementById("copy-toast");
//   if (existing) existing.remove();

//   // Create toast element
//   const toast = document.createElement("div");
//   toast.id = "copy-toast";
//   toast.className = "fixed top-4 right-4 bg-green-600 text-white px-4 py-2 rounded shadow-lg text-sm";
//   toast.textContent = message;

//   document.body.appendChild(toast);

//   // Auto-remove after 3 seconds
//   setTimeout(() => {
//     toast.remove();
//   }, 3000);
// }

// Copy username
function copyUsername(value) {
  navigator.clipboard.writeText(value);
  showCopyToast("Username copied");
}

// Copy password
function copyPassword(value) {
  navigator.clipboard.writeText(value);
  showCopyToast("Password copied");
}

function startPasswordModal(modalEl) {
  let seconds = 20;

  const countdownEl = modalEl.querySelector("#pw-countdown");
  const progressEl = modalEl.querySelector("#pw-progress");

  if (!countdownEl || !progressEl) return;

  // Reset UI
  countdownEl.textContent = seconds;
  progressEl.style.width = "100%";

  // Clear any previous timer
  if (modalEl._pwTimer) {
    clearInterval(modalEl._pwTimer);
  }

  modalEl._pwTimer = setInterval(() => {
    seconds -= 1;
    countdownEl.textContent = seconds;

    // Update progress bar
    const pct = (seconds / 20) * 100;
    progressEl.style.width = pct + "%";

    if (seconds <= 0) {
      clearInterval(modalEl._pwTimer);
      modalEl.remove();
    }
  }, 1000);
}

async function copyBreakglassPassword(reqId) {
  try {
    const resp = await fetch(`/ui/requests/${reqId}/copy-password`, {
      method: "GET",
      credentials: "include",
      headers: { "HX-Request": "true" }
    });

    const data = await resp.json();
    if (!data.ok || !data.password) {
      showCopyToast("Error copying password");
      return;
    }

    await navigator.clipboard.writeText(data.password);
    showCopyToast("Password copied");
  } catch (e) {
    showCopyToast("Clipboard error");
    console.error(e);
  }
}
