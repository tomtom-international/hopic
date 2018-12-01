Advantages:
 * Execute the exact same thing locally as on CI system
 * Easier for developers to locally reproduce problems
 * Developers don't need to learn any new programming languages
    - They already have to know C++, Python and YAML
 * Copy-pastable nature of commands shown in Jenkins' output
    - Can use interactive debugging of CI system's code

Easter egg advantages:
 * Declarative approach

Requirements on build system:
 * Allow overriding of version used by client's build system via command line
   - Allows automatically generating user-specific pre-release tags
   - Defaulting to ${CUR_VERSION} -> bump patch -> set pre-release to "$(git describe --tags --long --dirty --always)-${USER}"

Requirements for ci-driver:
 * Build config should not be executable
 - Maybe: allow adding an extra "SNAPSHOT pre-release bump" commit _after_ the commit created to build
 * Put all (or most) customization in the YAML config, avoiding the CLI or Jenkinsfile for this purpose
