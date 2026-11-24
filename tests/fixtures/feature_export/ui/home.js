// Example panel for the home slot. duo.call reaches this feature's own routes.
export default async function (el, duo) {
  const { runs } = await duo.call("GET", "/runs");
  const h = document.createElement("p");
  h.className = "hint";
  h.textContent = runs.length ? "Runs this session: " + runs.map(r => r.id + " (" + r.status + ")").join(", ") : "No runs yet in this session.";
  el.append(h);
}
