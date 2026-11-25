// The real cards Blast Radius sends, one per held kind. Generated from the hook; hooks.test.ts compares to it and
// global/.claude/hooks/tests/test_ask_card_guard.py runs every card through ask-card-guard.py. After a wording change: update this file.
// The Python test reads everything after "export default" as JSON, so keep it plain JSON.
export default {
  "rm -rf": {
    "questions": [
      {
        "question": "Blast Radius stopped a command before it ran:\n- rm -rf deletes the named files and folders and everything inside them, without asking for each one. Held because there is no undo, and a wrong or empty path deletes far more than meant.\nIt would touch:\nrm -rf: rm -rf /home/t/afa-mods-test\n  /home/t/afa-mods-test (dir, 0 bytes, 2 entries at the top level, all removed)\nRun it?",
        "header": "Plugin",
        "options": [
          {
            "label": "Hold, ask me again",
            "description": ""
          },
          {
            "label": "2 - Run it",
            "description": ""
          }
        ],
        "multiSelect": false
      }
    ]
  },
  "docker system prune": {
    "questions": [
      {
        "question": "Blast Radius stopped a command before it ran:\n- docker system prune deletes every unused Docker container, network and image on this machine. Held because one Docker engine is shared by every project here, and another project may need what it removes.\nIt would touch:\ndocker system prune: docker system prune -af\n  can't tell what this touches: docker did not answer (is the Docker engine running?)\nRun it?",
        "header": "Plugin",
        "options": [
          {
            "label": "Hold, ask me again",
            "description": ""
          },
          {
            "label": "2 - Run it",
            "description": ""
          }
        ],
        "multiSelect": false
      }
    ]
  },
  "docker image prune": {
    "questions": [
      {
        "question": "Blast Radius stopped a command before it ran:\n- docker image prune deletes every unused Docker container, network and image on this machine. Held because one Docker engine is shared by every project here, and another project may need what it removes.\nIt would touch:\ndocker image prune: docker image prune -a\n  can't tell what this touches: docker did not answer (is the Docker engine running?)\nRun it?",
        "header": "Plugin",
        "options": [
          {
            "label": "Hold, ask me again",
            "description": ""
          },
          {
            "label": "2 - Run it",
            "description": ""
          }
        ],
        "multiSelect": false
      }
    ]
  },
  "docker volume prune": {
    "questions": [
      {
        "question": "Blast Radius stopped a command before it ran:\n- docker volume prune deletes every Docker volume no running container uses (volumes hold databases and uploads). Held because the data in a deleted volume cannot be brought back.\nIt would touch:\ndocker volume prune: docker volume prune -f\n  can't tell what this touches: docker did not answer (is the Docker engine running?)\nRun it?",
        "header": "Plugin",
        "options": [
          {
            "label": "Hold, ask me again",
            "description": ""
          },
          {
            "label": "2 - Run it",
            "description": ""
          }
        ],
        "multiSelect": false
      }
    ]
  },
  "docker volume rm": {
    "questions": [
      {
        "question": "Blast Radius stopped a command before it ran:\n- docker volume rm deletes the named Docker volumes and the data in them. Held because the data cannot be brought back.\nIt would touch:\ndocker volume rm: docker volume rm somevol\n  can't tell what this touches: docker did not answer (is the Docker engine running?)\nRun it?",
        "header": "Plugin",
        "options": [
          {
            "label": "Hold, ask me again",
            "description": ""
          },
          {
            "label": "2 - Run it",
            "description": ""
          }
        ],
        "multiSelect": false
      }
    ]
  },
  "docker compose down -v": {
    "questions": [
      {
        "question": "Blast Radius stopped a command before it ran:\n- docker compose down -v stops the compose stack and deletes its volumes (its database and uploads). Held because the data in a deleted volume cannot be brought back.\nIt would touch:\ndocker compose down -v: docker compose -p x down -v\n  can't tell what this touches: the compose file here could not be read (docker compose config --volumes gave nothing), so the stack's volumes are unknown\nRun it?",
        "header": "Plugin",
        "options": [
          {
            "label": "Hold, ask me again",
            "description": ""
          },
          {
            "label": "2 - Run it",
            "description": ""
          }
        ],
        "multiSelect": false
      }
    ]
  },
  "docker run without limits": {
    "questions": [
      {
        "question": "Blast Radius stopped a command before it ran:\n- docker run without limits starts a container with no CPU cap and no timeout in front, so it can take every core and keep them. Held because on 19 Oct one such container took 14.6 of 16 cores and the machine stopped answering.\nIt would touch:\ndocker run without limits: docker run -d nginx\n  container from image or service nginx: no --cpus or compose cpus limit and no timeout, so it can use every core on this machine\n  fix: add --cpus N (and a timeout in front), as \"timeout <secs> docker run --cpus <n> --memory <m> ...\", or run it through \"~/.claude/scripts/docker-heavy.sh -- docker ...\"\nRun it?",
        "header": "Plugin",
        "options": [
          {
            "label": "Hold, ask me again",
            "description": ""
          },
          {
            "label": "2 - Run it",
            "description": ""
          }
        ],
        "multiSelect": false
      }
    ]
  },
  "git push --force": {
    "questions": [
      {
        "question": "Blast Radius stopped a command before it ran:\n- git push --force overwrites the branch on the remote with this machine's history. Held because commits other people or machines pushed can be lost.\nIt would touch:\ngit push --force: git push --force origin main\n  branch main on remote origin (its address could not be read here); commits there that this machine lacks are lost\nRun it?",
        "header": "Plugin",
        "options": [
          {
            "label": "Hold, ask me again",
            "description": ""
          },
          {
            "label": "2 - Run it",
            "description": ""
          }
        ],
        "multiSelect": false
      }
    ]
  },
  "production deploy": {
    "questions": [
      {
        "question": "Blast Radius stopped a command before it ran:\n- production deploy ships to the live production site. Held because production changes wait for the agreed window.\nIt would touch:\nproduction deploy: timeout 120 scripts/portainer-deploy.sh redeploy --prod\n  service: the Portainer stack deploy script (redeploy); target: production (--prod)\nRun it?",
        "header": "Plugin",
        "options": [
          {
            "label": "Hold, ask me again",
            "description": ""
          },
          {
            "label": "2 - Run it",
            "description": ""
          }
        ],
        "multiSelect": false
      }
    ]
  },
  "tmux shared lanes": {
    "questions": [
      {
        "question": "Blast Radius stopped a command before it ran:\n- tmux shared lanes closes every lane window (the tmux session 'lanes'), with no undo. Held because all lanes share this tmux session, so ending it closes every lane.\nIt would touch:\ntmux shared lanes: tmux kill-session -t lanes\n  closes every lane window (the tmux session 'lanes'), with no undo\nRun it?",
        "header": "Plugin",
        "options": [
          {
            "label": "Hold, ask me again",
            "description": ""
          },
          {
            "label": "2 - Run it",
            "description": ""
          }
        ],
        "multiSelect": false
      }
    ]
  }
}
