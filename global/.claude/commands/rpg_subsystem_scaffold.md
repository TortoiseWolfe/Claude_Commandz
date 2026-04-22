# RPG Subsystem Scaffold Command

This command helps you quickly scaffold a new subsystem for the RPG-Suite plugin following the established patterns.

## Usage
```
/rpg_subsystem_scaffold <subsystem_name> <description>
```

Example:
```
/rpg_subsystem_scaffold Combat "Implements turn-based combat with initiative tracking and combat logs"
```

## Command Actions

1. Creates the directory structure following RPG-Suite patterns:
   - src/<SubsystemName>/
   - src/<SubsystemName>/class-<subsystem-name>.php

2. Implements the base subsystem class with standard methods:
   - constructor
   - init
   - register_admin_hooks
   - register_public_hooks
   - add_admin_menu
   - register_settings
   - render_admin_page
   - get_subscribed_events

3. The class will follow the established event subscription pattern and integrate with the core system.

4. The scaffolded subsystem will be compatible with the existing RPG-Suite plugin architecture.

5. After scaffolding, you can continue with specific implementation details for your subsystem.