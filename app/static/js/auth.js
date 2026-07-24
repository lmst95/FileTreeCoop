// Login-/Registrierungs-Seite.

const errorEl = document.getElementById("auth-error");

function showError(msg) {
  errorEl.textContent = msg;
  errorEl.hidden = false;
}

// Tab-Umschaltung Anmelden <-> Registrieren.
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const name = tab.dataset.tab;
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    document.getElementById("login-form").classList.toggle("active", name === "login");
    document.getElementById("register-form").classList.toggle("active", name === "register");
    errorEl.hidden = true;
  });
});

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    await api("/api/auth/login", {
      method: "POST",
      body: { identifier: f.get("identifier"), password: f.get("password") },
    });
    window.location.href = "/";
  } catch (err) {
    showError(err.message);
  }
});

document.getElementById("register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    await api("/api/auth/register", {
      method: "POST",
      body: {
        display_name: f.get("display_name"),
        username: f.get("username"),
        email: f.get("email"),
        password: f.get("password"),
      },
    });
    window.location.href = "/";
  } catch (err) {
    showError(err.message);
  }
});
