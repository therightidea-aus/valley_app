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
