//  Validate form password
function validateForm() {
  const pw = document.getElementById("password").value.trim();
  const cpw = document.getElementById("confirm_password").value.trim();
  const error = document.getElementById("password_error");

  if (pw !== cpw) {
    error.textContent = "Passwords do not match.";
    error.classList.remove("hidden");
    return false;
  }

  error.classList.add("hidden");
  return true;
}

