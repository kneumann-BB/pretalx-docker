// Loads the pretix ticket table after the page shell has rendered, and shows a
// spinner on buttons whose request waits for pretix.

const SPINNER_CLASS = "fa fa-circle-o-notch fa-spin"

const showBusy = (el) => {
  if (!el || el.dataset.busy) return
  el.dataset.busy = "1"
  el.classList.add("disabled")
  el.setAttribute("aria-busy", "true")
  const icon = el.querySelector("i.fa")
  if (icon) {
    icon.className = SPINNER_CLASS
  } else {
    const spinner = document.createElement("i")
    spinner.className = `${SPINNER_CLASS} mr-1`
    el.prepend(spinner)
  }
}

const showError = (container) => {
  const alert = document.createElement("div")
  alert.className = "alert alert-danger"
  alert.textContent = container.dataset.error || "Could not load the ticket list."
  const retry = document.createElement("a")
  retry.href = window.location.href
  retry.className = "alert-link ml-2"
  retry.textContent = container.dataset.retry || "Try again"
  alert.append(retry)
  container.replaceChildren(alert)
}

const loadTable = async (container) => {
  try {
    const response = await fetch(container.dataset.url, {
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
    // A redirect means the session expired: reload to get the login page
    if (response.redirected) return window.location.reload()
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    container.innerHTML = await response.text()
  } catch (e) {
    console.error("pretix ticket table failed to load", e)
    showError(container)
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const container = document.getElementById("pretix-tickets")
  if (container) loadTable(container)

  // The table request above already carries ?refresh=1; drop it from the address
  // bar so reloading the page uses the cache instead of refetching from pretix
  const url = new URL(window.location.href)
  if (url.searchParams.has("refresh")) {
    url.searchParams.delete("refresh")
    window.history.replaceState(null, "", url)
  }

  // Delegated, because the table's forms are inserted after load
  document.addEventListener("submit", (event) => {
    const form = event.target
    if (form.dataset.submitting) return event.preventDefault()
    const button = event.submitter
    if (button?.hasAttribute("data-pretix-busy")) {
      form.dataset.submitting = "1"
      showBusy(button)
    }
  })
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-pretix-busy]")
    if (link) showBusy(link)
  })
})
