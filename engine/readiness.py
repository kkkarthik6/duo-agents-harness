def checks(agents, docker, tmux):
    items = []
    for agent_id, state in agents.items():
        found = state["found"]
        signed_in = state["signed_in"]
        ok = found and signed_in is not False
        if not found:
            fix = "Install the %s CLI, then press Check again." % agent_id
        elif signed_in is False:
            fix = "Open Terminal, run %s and sign in, then press Continue." % agent_id
        else:
            fix = ""
        items.append({"id": "agent:" + agent_id, "label": "%s CLI" % agent_id,
                      "ok": ok, "fix": fix})

    docker_ok, docker_message = docker
    items.append({
        "id": "docker",
        "label": "Docker" if docker_ok else "Docker: " + docker_message,
        "ok": docker_ok,
        "fix": "" if docker_ok else "Start Docker Desktop, then press Check again.",
    })

    items.append({
        "id": "tmux",
        "label": "tmux",
        "ok": tmux,
        "fix": "" if tmux else "Install tmux with: brew install tmux",
    })
    return items


def login_card(agent_id):
    return {
        "title": "Sign in to " + agent_id,
        "steps": [
            "Open Terminal.",
            "Run %s and sign in." % agent_id,
            "Come back here and press Continue.",
        ],
    }
