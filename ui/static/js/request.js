
function approveRequest(id) {
  fetch(`/api/requests/${id}/approve`, {
    method: "POST",
    credentials: "include"
  })
  .then(r => r.json())
  .then(data => location.reload());
}


document.getElementById('toast-container')
  .addEventListener('htmx:afterSwap', () => {
      // When toast is inserted, start countdown
      setTimeout(() => {
          // Clear toast
          document.getElementById('toast-container').innerHTML = '';

          // Reload page
          window.location.reload();
      }, 2000);
  });