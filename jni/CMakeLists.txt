# CMake for the Android library build.
#
# Drop this directory into an Android Studio project as
# app/src/main/cpp/veritpath/ and add:
#
#   add_subdirectory(veritpath)
#   target_link_libraries(your-lib PRIVATE veritpath)
#
# Or build standalone:
#   cmake -S jni -B build-jni -DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake \
#         -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-24
#   cmake --build build-jni

cmake_minimum_required(VERSION 3.10.2)
project(veritpath C)

set(CMAKE_C_STANDARD 11)

file(GLOB VP_SOURCES ${CMAKE_CURRENT_SOURCE_DIR}/../src/*.c)
set(VP_JNI ${CMAKE_CURRENT_SOURCE_DIR}/veritpath_jni.c)

# Without VP_NO_MAIN the CLI's main() would be linked into the shared object.
add_library(veritpath SHARED ${VP_SOURCES} ${VP_JNI})
target_compile_definitions(veritpath PRIVATE VP_NO_MAIN)
target_include_directories(veritpath PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/../src)
target_compile_options(veritpath PRIVATE -Wall -Wextra -O2)

find_library(ZLIB zlib)
if(ZLIB)
    target_link_libraries(veritpath ${ZLIB})
else()
    target_link_libraries(veritpath z)
endif()

# plain static library, for whoever prefers linking it in directly
add_library(veritpath_static STATIC ${VP_SOURCES})
target_compile_definitions(veritpath_static PRIVATE VP_NO_MAIN)
target_include_directories(veritpath_static PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/../src)
target_compile_options(veritpath_static PRIVATE -Wall -Wextra -O2)
