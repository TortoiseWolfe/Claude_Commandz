# Repository Context Priming Command

This command loads essential context about the RPG-Suite and ZZZ repositories to help Claude understand the project structure quickly.

```bash
# Load main readme files
read_file "/home/turtle_wolfe/repos/two_Tubes/RPG-Suite/README.md"
read_file "/home/turtle_wolfe/repos/two_Tubes/ZZZ/README.md"

# Load integration plans
read_file "/home/turtle_wolfe/repos/two_Tubes/RPG_SUITE_INTEGRATION_PLAN.md"
read_file "/home/turtle_wolfe/repos/two_Tubes/RPG-SUITE_TEST_PLAN.md"

# Core RPG-Suite files
read_file "/home/turtle_wolfe/repos/two_Tubes/RPG-Suite/rpg-suite.php"
read_file "/home/turtle_wolfe/repos/two_Tubes/RPG-Suite/includes/class-rpg-suite.php"
read_file "/home/turtle_wolfe/repos/two_Tubes/RPG-Suite/includes/class-character-manager.php"

# Core ZZZ files
read_file "/home/turtle_wolfe/repos/two_Tubes/ZZZ/ARCHITECTURE.md"
read_file "/home/turtle_wolfe/repos/two_Tubes/ZZZ/docker-compose.yml"
```

Use this command to quickly load essential project context without having to manually request each file.