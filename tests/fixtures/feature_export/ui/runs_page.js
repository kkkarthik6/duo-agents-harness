// Example full page (contract 1.2). Core adds a side bar button "Runs"; this module fills the main view.
export default async function (el, duo) {
  const h = document.createElement("h2");
  h.textContent = "Runs";
  const list = document.createElement("ul");
  const { runs } = await duo.call("GET", "/runs");
  for (const r of runs) {
    const li = document.createElement("li");
    li.textContent = r.id + ": " + r.task + " (" + r.status + ")";
    list.append(li);
  }
  if (!runs.length) list.textContent = "No runs yet in this session.";
  el.append(h, list);
}
