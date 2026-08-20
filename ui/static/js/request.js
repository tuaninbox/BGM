
function approveRequest(id) {
  fetch(`/api/requests/${id}/approve`, {
    method: "POST",
    credentials: "include"
  })
  .then(r => r.json())
  .then(data => location.reload());
}

