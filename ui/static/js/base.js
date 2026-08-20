// These codes are for debugging
document.body.addEventListener("htmx:responseError", (evt) => {
    const detail = evt.detail;
    console.error("HTMX Error:", detail);

    // Show error inline
    const modal = document.querySelector("#modal") || document.querySelector("#inline-popup");
    if (modal) {
        modal.innerHTML = `
            <div class="p-4 bg-red-100 border border-red-400 text-red-700 rounded">
                <h2 class="font-bold mb-2">HTMX Error</h2>
                <p>Status: ${detail.xhr.status}</p>
                <p>${detail.xhr.responseText}</p>
            </div>
        `;
    }
});

document.body.addEventListener("htmx:beforeRequest", (evt) => {
    console.log("HTMX Request:", evt.detail.requestConfig);
});
document.body.addEventListener("htmx:afterRequest", (evt) => {
    console.log("HTMX Response:", evt.detail.xhr);
});