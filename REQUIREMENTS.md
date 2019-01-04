High level requirements

 * Build config should not be executable (not Turing complete)
 * All project-specific customization should happen in the configuration file
  - Avoiding the CLI, Groovy and Python code for this purpose
 * Versioning policy should be configurable
  - Policy should provide:
   + ordering (not sure whether should be total or partial)
   + syntax (parsing and serialization)
   + bumping strategies
 * Provide means to automatically generate user-specific versions
  - To prevent accidental collisions and confusion over those

Requirements on build system:
 * Allow overriding of version used by client's build system via command line
   - Allows automatically generating user-specific pre-release tags
   - Defaulting to ${CUR_VERSION} -> bump patch -> set pre-release to "$(git describe --tags --long --dirty --always)-${USER}"
