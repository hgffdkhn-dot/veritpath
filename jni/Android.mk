# ndk-build support.
#
#   ndk-build NDK_PROJECT_PATH=. APP_BUILD_SCRIPT=./jni/Android.mk
#
# or from an Android Studio project that already uses ndk-build.
LOCAL_PATH := $(call my-dir)
VP_ROOT    := $(LOCAL_PATH)/..

include $(CLEAR_VARS)
LOCAL_MODULE    := veritpath
LOCAL_SRC_FILES := $(wildcard $(VP_ROOT)/src/*.c) $(VP_ROOT)/jni/veritpath_jni.c
LOCAL_C_INCLUDES := $(VP_ROOT)/src
LOCAL_CFLAGS    := -O2 -std=c11 -Wall -Wextra -DVP_NO_MAIN
LOCAL_LDLIBS    := -lz
include $(BUILD_SHARED_LIBRARY)

include $(CLEAR_VARS)
LOCAL_MODULE    := veritpath_static
LOCAL_SRC_FILES := $(wildcard $(VP_ROOT)/src/*.c)
LOCAL_C_INCLUDES := $(VP_ROOT)/src
LOCAL_CFLAGS    := -O2 -std=c11 -Wall -Wextra -DVP_NO_MAIN
include $(BUILD_STATIC_LIBRARY)
