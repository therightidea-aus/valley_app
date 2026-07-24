let serviceWorkerRegistration = null;

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/service-worker.js")
      .then((registration) => {
        serviceWorkerRegistration = registration;
        setupPushControls(registration);
      })
      .catch(() => {
        setupPushControls(null);
      });
  });
} else {
  window.addEventListener("load", () => setupPushControls(null));
}

window.addEventListener("DOMContentLoaded", () => {
  setupFeedLightbox();
  setupFeedComposer();
});

function setupFeedComposer() {
  document.querySelectorAll("[data-feed-form]").forEach((form) => {
    const fileInput = form.querySelector("[data-feed-files]");
    const status = form.querySelector("[data-feed-status]");
    const submit = form.querySelector("[data-feed-submit]");
    const errors = form.querySelector("[data-feed-errors]");
    const card = form.closest(".feed-composer");

    if (fileInput && status) {
      fileInput.addEventListener("change", () => {
        const count = fileInput.files.length;
        status.textContent = count ? `${count} photo${count === 1 ? "" : "s"} selected` : "No photos selected";
      });
    }

    form.addEventListener("submit", async (event) => {
      if (!window.fetch || !window.FormData) return;
      event.preventDefault();
      setFeedFormState(card, submit, status, errors, true, "Uploading photos...");

      try {
        const response = await fetch(form.action || window.location.href, {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
          },
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
          throw new Error((payload.errors || ["Feed post could not be saved."]).join(" "));
        }
        setFeedFormState(card, submit, status, errors, true, "Post saved. Refreshing...");
        window.location.href = payload.redirect_url || window.location.href;
      } catch (error) {
        showFeedErrors(errors, error.message || "Feed post could not be saved.");
        setFeedFormState(card, submit, status, errors, false, "Try again");
      }
    });
  });
}

function setFeedFormState(card, submit, status, errors, disabled, message) {
  if (card) card.classList.toggle("is-uploading", disabled);
  if (submit) submit.disabled = disabled;
  if (status) status.textContent = message;
  if (errors && disabled) {
    errors.hidden = true;
    errors.textContent = "";
  }
}

function showFeedErrors(errors, message) {
  if (!errors) return;
  errors.textContent = message;
  errors.hidden = false;
}

function setupFeedLightbox() {
  const dialog = document.querySelector("[data-lightbox]");
  if (!dialog) return;
  const image = dialog.querySelector("[data-lightbox-image]");
  const closeButton = dialog.querySelector("[data-lightbox-close]");

  document.querySelectorAll("[data-lightbox-src]").forEach((button) => {
    button.addEventListener("click", () => {
      image.src = button.dataset.lightboxSrc;
      image.alt = button.dataset.lightboxAlt || "";
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      }
    });
  });

  closeButton.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
}

function setupPushControls(registration) {
  const button = document.querySelector("[data-push-toggle]");
  if (!button) return;

  const status = document.querySelector("[data-push-status]");
  const publicKey = button.dataset.vapidPublicKey;
  const isSupported = Boolean(registration && "PushManager" in window && "Notification" in window);

  if (!publicKey) {
    setPushUi(button, status, "Setup pending", "Enable", true);
    return;
  }

  if (!isSupported) {
    setPushUi(button, status, "Push notifications are not supported on this device", "Enable", true);
    return;
  }

  registration.pushManager.getSubscription().then((subscription) => {
    setPushUi(
      button,
      status,
      subscription ? "Enabled on this device" : "Get notified when you are added to a roster",
      subscription ? "Disable" : "Enable",
      false
    );
  });

  button.addEventListener("click", () => togglePushSubscription(registration, button, status));
}

async function togglePushSubscription(registration, button, status) {
  setPushUi(button, status, "Updating notification settings...", button.textContent, true);
  const existingSubscription = await registration.pushManager.getSubscription();

  if (existingSubscription) {
    await postJson(button.dataset.unsubscribeUrl, button.dataset.csrfToken, existingSubscription.toJSON());
    await existingSubscription.unsubscribe();
    setPushUi(button, status, "Push notifications disabled on this device", "Enable", false);
    return;
  }

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    setPushUi(button, status, "Notifications were not enabled", "Enable", false);
    return;
  }

  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(button.dataset.vapidPublicKey),
  });
  await postJson(button.dataset.subscribeUrl, button.dataset.csrfToken, subscription.toJSON());
  setPushUi(button, status, "Enabled on this device", "Disable", false);
}

function setPushUi(button, status, message, label, disabled) {
  if (status) status.textContent = message;
  button.textContent = label;
  button.disabled = disabled;
}

function postJson(url, csrfToken, payload) {
  return fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken,
    },
    body: JSON.stringify(payload),
  }).then((response) => {
    if (!response.ok) throw new Error("Push settings could not be saved.");
    return response.json();
  });
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);

  for (let i = 0; i < rawData.length; i += 1) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}
