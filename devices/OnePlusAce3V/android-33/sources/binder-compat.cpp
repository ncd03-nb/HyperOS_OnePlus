// Donor-specific Android14 Binder NDK backport for MIUI14 SDK33.
// Semantics: AOSP android14-release service_manager.cpp/process.cpp.
// ABI offsets are verified against the exact donor libbinder/libbinder_ndk hashes
// by build-binder-compat.py; this library must not be reused on another donor.
#include <stdint.h>
#include <stddef.h>
#include <pthread.h>

extern "C" void ref_dec(const void*, const void*) asm("_ZNK7android7RefBase9decStrongEPKv");
struct StrongResult {
    void* value = nullptr;
    StrongResult() = default;
    StrongResult(const StrongResult&) = delete;
    ~StrongResult() {
        if (value) {
            auto vtable = *reinterpret_cast<ptrdiff_t**>(value);
            auto base = static_cast<const char*>(value) + vtable[-3];
            ref_dec(base, this);
        }
    }
};
extern "C" StrongResult service_manager() asm("_ZN7android21defaultServiceManagerEv");
extern "C" StrongResult process_state() asm("_ZN7android12ProcessState4selfEv");
extern "C" void string16_init(void*, const char*) asm("_ZN7android8String16C1EPKc");
extern "C" void string16_destroy(void*) asm("_ZN7android8String16D1Ev");
struct String16Result {
    void* storage;
    explicit String16Result(const char* s) { string16_init(this, s); }
    ~String16Result() { string16_destroy(this); }
};
static int prune_exception(int status) {
    switch (status) {
        case 0: case -1: case -2: case -3: case -4: case -5:
        case -6: case -7: case -8: case -9: case -129: return status;
        default: return -129;
    }
}
extern "C" __attribute__((visibility("default")))
int AServiceManager_addServiceWithFlags(void* binder, const char* instance, unsigned flags) {
    if (!binder || !instance) return -3;
    auto sm = service_manager();
    String16Result name(instance);
    using GetBinder = StrongResult (*)(void*);
    auto get = reinterpret_cast<GetBinder>((*reinterpret_cast<void***>(binder))[2]);
    auto object = get(binder);
    using AddService = int (*)(void*, const void*, const StrongResult&, bool, int);
    auto add = reinterpret_cast<AddService>((*reinterpret_cast<void***>(sm.value))[6]);
    // Android14's only flag is ADD_SERVICE_ALLOW_ISOLATED=1; retain it.
    return prune_exception(add(sm.value, &name, object, (flags & 1u) != 0, 8));
}
extern "C" __attribute__((visibility("default")))
bool ABinderProcess_isThreadPoolStarted() {
    auto state = process_state();
    auto bytes = static_cast<unsigned char*>(state.value);
    // Matches this donor's ProcessState::startThreadPool: lock+0x98, bool+0xe9.
    auto lock = reinterpret_cast<pthread_mutex_t*>(bytes + 0x98);
    pthread_mutex_lock(lock);
    bool started = bytes[0xe9] != 0;
    pthread_mutex_unlock(lock);
    return started;
}
