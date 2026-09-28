# StratumInit.cmake — injected via CMAKE_PROJECT_TOP_LEVEL_INCLUDES
# Tells nanobind where host Python interpreter and headers live.
# Written to the per-ABI build folder with absolute paths — same pattern
# as DroidbindInit.cmake in lvpy. Never a relative path.

if(NOT TARGET Python::Module)
  add_library(Python::Module INTERFACE IMPORTED GLOBAL)
  set_target_properties(Python::Module PROPERTIES
      INTERFACE_INCLUDE_DIRECTORIES "{{INC_ABS}}")
endif()

if(NOT TARGET Python::Interpreter)
  add_executable(Python::Interpreter IMPORTED GLOBAL)
  set_target_properties(Python::Interpreter PROPERTIES
      IMPORTED_LOCATION "{{HOST_PYTHON}}")
endif()

set(Python_EXECUTABLE            "{{HOST_PYTHON}}" CACHE FILEPATH "" FORCE)
set(Python3_EXECUTABLE           "{{HOST_PYTHON}}" CACHE FILEPATH "" FORCE)
set(NB_SUFFIX                    ".so"             CACHE STRING   "" FORCE)
set(NB_SUFFIX_S                  ".so"             CACHE STRING   "" FORCE)
set(Python_VERSION               "{{PY_VER}}"      CACHE STRING   "" FORCE)
set(Python_VERSION_MAJOR         "{{PY_MAJOR}}"    CACHE STRING   "" FORCE)
set(Python_VERSION_MINOR         "{{PY_MINOR}}"    CACHE STRING   "" FORCE)
set(Python_INCLUDE_DIRS          "{{INC_ABS}}"     CACHE PATH     "" FORCE)
set(Python_FOUND                 TRUE CACHE BOOL "" FORCE)
set(Python_Development_FOUND     TRUE CACHE BOOL "" FORCE)
set(Python_Development.Module_FOUND TRUE CACHE BOOL "" FORCE)
set(Python_Interpreter_FOUND     TRUE CACHE BOOL "" FORCE)
