cmake_minimum_required(VERSION 3.21)
project(RightConvertExplorer LANGUAGES CXX)

if(NOT WIN32)
    message(FATAL_ERROR "Windows shell extension can only be built on Windows.")
endif()

add_library(RightConvertExplorer SHARED RightConvertExplorer.cpp RightConvertExplorer.def)
target_compile_features(RightConvertExplorer PRIVATE cxx_std_17)
target_compile_definitions(RightConvertExplorer PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
target_link_libraries(RightConvertExplorer PRIVATE ole32 shell32 shlwapi)
set_target_properties(RightConvertExplorer PROPERTIES
    OUTPUT_NAME "RightConvertExplorer"
    MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>"
)
if(MSVC)
    target_compile_options(RightConvertExplorer PRIVATE /W4 /EHsc /permissive-)
    target_link_options(RightConvertExplorer PRIVATE /DYNAMICBASE /NXCOMPAT)
endif()

include(CTest)
if(BUILD_TESTING)
    add_executable(RightConvertExplorerSmoke SmokeTest.cpp)
    target_compile_features(RightConvertExplorerSmoke PRIVATE cxx_std_17)
    target_compile_definitions(RightConvertExplorerSmoke PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
    target_link_libraries(RightConvertExplorerSmoke PRIVATE ole32)
    if(MSVC)
        target_compile_options(RightConvertExplorerSmoke PRIVATE /W4 /EHsc /permissive-)
    endif()
    add_test(NAME RightConvertExplorerComSmoke
             COMMAND RightConvertExplorerSmoke $<TARGET_FILE:RightConvertExplorer>)
endif()
