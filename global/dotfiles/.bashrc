# OPENSPEC:START
# OpenSpec shell completions configuration
if [ -d "/home/turtle_wolfe/.local/share/bash-completion/completions" ]; then
  for f in "/home/turtle_wolfe/.local/share/bash-completion/completions"/*; do
    [ -f "$f" ] && . "$f"
  done
fi
# OPENSPEC:END

# ~/.bashrc: executed by bash(1) for non-login shells.
# see /usr/share/doc/bash/examples/startup-files (in the package bash-doc)
# for examples

# If not running interactively, don't do anything
case $- in
    *i*) ;;
      *) return;;
esac

# Add spec-kit-docker to PATH
export PATH="$HOME/spec-kit-docker:$PATH"

# don't put duplicate lines or lines starting with space in the history.
# See bash(1) for more options
HISTCONTROL=ignoreboth

# append to the history file, don't overwrite it
shopt -s histappend

# for setting history length see HISTSIZE and HISTFILESIZE in bash(1)
HISTSIZE=1000
HISTFILESIZE=2000

# check the window size after each command and, if necessary,
# update the values of LINES and COLUMNS.
shopt -s checkwinsize

# If set, the pattern "**" used in a pathname expansion context will
# match all files and zero or more directories and subdirectories.
#shopt -s globstar

# make less more friendly for non-text input files, see lesspipe(1)
#[ -x /usr/bin/lesspipe ] && eval "$(SHELL=/bin/sh lesspipe)"

# set variable identifying the chroot you work in (used in the prompt below)
if [ -z "${debian_chroot:-}" ] && [ -r /etc/debian_chroot ]; then
    debian_chroot=$(cat /etc/debian_chroot)
fi

# set a fancy prompt (non-color, unless we know we "want" color)
case "$TERM" in
    xterm-color|*-256color) color_prompt=yes;;
esac

# uncomment for a colored prompt, if the terminal has the capability; turned
# off by default to not distract the user: the focus in a terminal window
# should be on the output of commands, not on the prompt
#force_color_prompt=yes

if [ -n "$force_color_prompt" ]; then
    if [ -x /usr/bin/tput ] && tput setaf 1 >&/dev/null; then
	# We have color support; assume it's compliant with Ecma-48
	# (ISO/IEC-6429). (Lack of such support is extremely rare, and such
	# a case would tend to support setf rather than setaf.)
	color_prompt=yes
    else
	color_prompt=
    fi
fi

if [ "$color_prompt" = yes ]; then
    PS1='${debian_chroot:+($debian_chroot)}\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '
else
    PS1='${debian_chroot:+($debian_chroot)}\u@\h:\w\$ '
fi
unset color_prompt force_color_prompt

# If this is an xterm set the title to user@host:dir
case "$TERM" in
xterm*|rxvt*)
    PS1="\[\e]0;${debian_chroot:+($debian_chroot)}\u@\h: \w\a\]$PS1"
    ;;
*)
    ;;
esac

# enable color support of ls and also add handy aliases
if [ -x /usr/bin/dircolors ]; then
    test -r ~/.dircolors && eval "$(dircolors -b ~/.dircolors)" || eval "$(dircolors -b)"
    alias ls='ls --color=auto'
    #alias dir='dir --color=auto'
    #alias vdir='vdir --color=auto'

    #alias grep='grep --color=auto'
    #alias fgrep='fgrep --color=auto'
    #alias egrep='egrep --color=auto'
fi

# colored GCC warnings and errors
#export GCC_COLORS='error=01;31:warning=01;35:note=01;36:caret=01;32:locus=01:quote=01'

# some more ls aliases
#alias ll='ls -l'
#alias la='ls -A'
#alias l='ls -CF'

# Alias definitions.
# You may want to put all your additions into a separate file like
# ~/.bash_aliases, instead of adding them here directly.
# See /usr/share/doc/bash-doc/examples in the bash-doc package.

if [ -f ~/.bash_aliases ]; then
    . ~/.bash_aliases
fi

# enable programmable completion features (you don't need to enable
# this, if it's already enabled in /etc/bash.bashrc and /etc/profile
# sources /etc/bash.bashrc).
if ! shopt -oq posix; then
  if [ -f /usr/share/bash-completion/bash_completion ]; then
    . /usr/share/bash-completion/bash_completion
  elif [ -f /etc/bash_completion ]; then
    . /etc/bash_completion
  fi
fi
alias ll='ls -alF'

# --- Begin Custom Prompt Setup with Docker Whale Icon, Local Time, Four-Line Multiline Prompt, and Emoji Username (Cleaned) ---

# Define color variables for a vibrant prompt.
RED="\[\033[0;31m\]"
GREEN="\[\033[0;32m\]"
YELLOW="\[\033[0;33m\]"
BLUE="\[\033[0;34m\]"
MAGENTA="\[\033[0;35m\]"
CYAN="\[\033[0;36m\]"
WHITE="\[\033[0;37m\]"
RESET="\[\033[0m\]"

# Function to extract the current Git branch name.
function parse_git_branch {
  git branch 2>/dev/null | sed -n '/\* /s///p'
}

# Function to check if running inside a Docker container.
function docker_indicator {
  if grep -qE '/docker|/lxc' /proc/1/cgroup 2>/dev/null; then
    echo "🐳"
  fi
}

# Function to dynamically set the Bash prompt.
function set_bash_prompt {
  # Get the Git branch (if any).
  local git_branch
  git_branch=$(parse_git_branch)
  if [ -n "$git_branch" ]; then
      git_branch=" ${YELLOW}(${git_branch})${RESET}"
  fi

  # Get Docker indicator (whale icon) if inside Docker.
  local docker_icon
  docker_icon=$(docker_indicator)
  if [ -n "$docker_icon" ]; then
      docker_icon=" ${MAGENTA}${docker_icon}${RESET}"
  fi

  # Get the local time in 12-hour format (hours and minutes with AM/PM).
  local local_time
  local_time=$(date +"%I:%M %p")

  # Wrap the username in a turtle and a wolf emoji.
  local emoji_user
  emoji_user="${GREEN}🐢 \u 🐺 ${RESET}"

  # Build the four-line prompt without seconds and brackets.
  PS1="\n${YELLOW}${local_time}${RESET}\n${emoji_user}@ ${BLUE}\h${RESET}${docker_icon}\n${CYAN}\w${RESET}${git_branch}\n\$ "
}

# Set the prompt command to update before each command.
PROMPT_COMMAND=set_bash_prompt

# --- End Custom Prompt Setup with Docker Whale Icon, Local Time, Four-Line Multiline Prompt, and Emoji Username (Cleaned) ---


export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion
alias code="code --no-sandbox"
export PATH="/home/turtle_wolfe/.nvm/versions/node/v20.18.3/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/usr/lib/wsl/lib:/mnt/c/WINDOWS/system32:/mnt/c/WINDOWS:/mnt/c/WINDOWS/System32/Wbem:/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/:/mnt/c/WINDOWS/System32/OpenSSH/:/Docker/host/bin:/mnt/c/Program Files/Git/cmd:/mnt/c/Users/JonPo/AppData/Local/Microsoft/WindowsApps:/mnt/c/Users/JonPo/AppData/Local/Programs/Microsoft VS Code/bin:/mnt/c/Users/JonPo/AppData/Local/Programs/cursor/resources/app/bin:~/.local/bin"
# Load secrets from .env (not tracked in git)
[ -f ~/.env ] && export $(grep -v '^#' ~/.env | xargs)

export PATH=~/.npm-global/bin:$PATH
