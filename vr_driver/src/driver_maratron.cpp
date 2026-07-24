// Maratron SteamVR (OpenVR) driver.
//
// Registers one virtual controller with our OWN controller type "maratron_treadmill"
// and drives its joystick from a shared-memory struct written by the Python app
// (see python/src/maratron/vr_ipc.py). Modeled on OpenVR-WalkInPlace: the device sits
// on the /user/treadmill path (Treadmill role) and ships an input profile whose
// legacy_binding (resources/input/) maps /user/treadmill/input/joystick -> the LEFT
// hand's axis0. That routes the treadmill stick into left-hand locomotion while BOTH
// real controllers keep working (keep-both). Role still comes from shared memory;
// treadmill (4) is the intended mode -- the profile's legacy paths assume /user/treadmill.
//
// This is a user-mode plugin loaded by vrserver.exe — no kernel-driver signing.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <openvr_driver.h>

#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>

using namespace vr;

// ---------------------------------------------------------------------------
// Shared memory — layout MUST match python/src/maratron/vr_ipc.py (packed LE).
// ---------------------------------------------------------------------------
#pragma pack(push, 1)
struct MaratronShared {
    uint32_t magic;      // 0x4D545652 ("MTVR")
    uint32_t version;    // 1
    uint64_t seq;        // bumped each write; staleness heartbeat
    double   timestamp;  // diagnostics only
    float    joyX;       // -1..1
    float    joyY;       // -1..1 (forward)
    float    trigger;    // 0..1
    uint32_t buttons;    // bit0=grip, bit1=unused, bit2=stickClick
    uint32_t role;       // ETrackedControllerRole: 1=left 2=right 3=optout 4=treadmill
};
#pragma pack(pop)

static const uint32_t kMagic = 0x4D545652u;
static const wchar_t* kShmName = L"Local\\MaratronVRInput";
static const DWORD    kShmSize = 48;             // region size (struct is 44, padded)
static const double   kStaleMs = 400.0;          // no new seq for this long -> zero out

// Button bits must match vr_ipc.py. bit 1 is unused (no jump input on the controller).
enum : uint32_t { BTN_SPRINT = 1u << 0, BTN_STICK_CLICK = 1u << 2 };

class SharedMem {
public:
    bool Open() {
        // Create (or open existing) so start order vs the Python app doesn't matter.
        m_map = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, kShmSize, kShmName);
        if (!m_map) return false;
        m_view = reinterpret_cast<MaratronShared*>(MapViewOfFile(m_map, FILE_MAP_READ, 0, 0, kShmSize));
        m_lastChange = std::chrono::steady_clock::now();
        return m_view != nullptr;
    }

    // Latest values; returns false (and zeros) when stale/invalid so the device can
    // report itself disconnected while Maratron isn't actively sending.
    bool Read(float& jx, float& jy, float& tr, uint32_t& btn) {
        jx = jy = tr = 0.0f; btn = 0;
        if (!m_view) return false;
        MaratronShared s;
        std::memcpy(&s, m_view, sizeof(s));
        if (s.magic != kMagic) return false;
        if (s.role != 0) m_role = s.role;  // remember configured role even when stale
        auto now = std::chrono::steady_clock::now();
        if (s.seq != m_lastSeq) { m_lastSeq = s.seq; m_lastChange = now; }
        double ageMs = std::chrono::duration<double, std::milli>(now - m_lastChange).count();
        if (ageMs > kStaleMs) return false;  // stale -> centered stick, disconnected
        jx = s.joyX; jy = s.joyY; tr = s.trigger; btn = s.buttons;
        return true;
    }

    // Configured SteamVR role (defaults to OptOut=3 until the app tells us otherwise).
    int32_t Role() const { return m_role ? (int32_t)m_role : 3; }
    uint64_t Seq() const { return m_lastSeq; }
    bool Mapped() const { return m_view != nullptr; }

    void Close() {
        if (m_view) UnmapViewOfFile(m_view);
        if (m_map) CloseHandle(m_map);
        m_view = nullptr; m_map = nullptr;
    }

private:
    HANDLE m_map = nullptr;
    MaratronShared* m_view = nullptr;
    uint64_t m_lastSeq = 0;
    uint32_t m_role = 0;
    std::chrono::steady_clock::time_point m_lastChange;
};

// ---------------------------------------------------------------------------
// The virtual controller device.
// ---------------------------------------------------------------------------
class TreadmillController : public ITrackedDeviceServerDriver {
public:
    explicit TreadmillController(SharedMem& shm) : m_shm(shm) {}

    const char* Serial() const { return "maratron-treadmill-L-0001"; }

    EVRInitError Activate(TrackedDeviceIndex_t unObjectId) override {
        m_id = unObjectId;
        m_props = VRProperties()->TrackedDeviceToPropertyContainer(unObjectId);

        // Our OWN controller type + input profile (resources/input/), modeled on
        // OpenVR-WalkInPlace: single_device profile whose legacy_binding routes
        // /user/treadmill/input/joystick -> the LEFT hand's axis0. Render model borrows
        // the Index model just so the device is visible in the overlay.
        VRProperties()->SetStringProperty(m_props, Prop_ControllerType_String, "maratron_treadmill");
        VRProperties()->SetStringProperty(m_props, Prop_InputProfilePath_String,
                                          "{maratron}/input/maratron_treadmill_profile.json");
        VRProperties()->SetStringProperty(m_props, Prop_ManufacturerName_String, "Maratron");
        VRProperties()->SetStringProperty(m_props, Prop_ModelNumber_String, "Treadmill");
        VRProperties()->SetStringProperty(m_props, Prop_RenderModelName_String,
                                          "{indexcontroller}valve_controller_knu_1_0_left");
        VRProperties()->SetStringProperty(m_props, Prop_RegisteredDeviceType_String,
                                          "maratron/treadmill_left");
        VRProperties()->SetStringProperty(m_props, Prop_SerialNumber_String, Serial());
        VRProperties()->SetStringProperty(m_props, Prop_TrackingSystemName_String, "maratron");

        // Role comes from the dashboard via shared memory (peek so we pick it up if the
        // app is already running). Left/Right make it a hand; Treadmill keeps controllers.
        { float a, b, c; uint32_t d; m_shm.Read(a, b, c, d); }
        m_currentRole = m_shm.Role();
        VRProperties()->SetInt32Property(m_props, Prop_ControllerRoleHint_Int32, m_currentRole);
        VRProperties()->SetInt32Property(m_props, Prop_DeviceClass_Int32, TrackedDeviceClass_Controller);
        VRProperties()->SetBoolProperty(m_props, Prop_DeviceIsWireless_Bool, true);

        auto& in = *VRDriverInput();
        // Component set must match maratron_treadmill_profile.json input sources.
        // /input/joystick/y is the locomotion axis (legacy binding -> left_axis0_value).
        in.CreateScalarComponent(m_props, "/input/joystick/x", &m_stickX,
                                 VRScalarType_Absolute, VRScalarUnits_NormalizedTwoSided);
        in.CreateScalarComponent(m_props, "/input/joystick/y", &m_stickY,
                                 VRScalarType_Absolute, VRScalarUnits_NormalizedTwoSided);
        in.CreateBooleanComponent(m_props, "/input/joystick/click", &m_stickClick);
        in.CreateBooleanComponent(m_props, "/input/joystick/touch", &m_stickTouch);
        in.CreateScalarComponent(m_props, "/input/trigger/value", &m_trigger,
                                 VRScalarType_Absolute, VRScalarUnits_NormalizedOneSided);
        in.CreateBooleanComponent(m_props, "/input/trigger/touch", &m_trigTouch);
        in.CreateBooleanComponent(m_props, "/input/grip/click", &m_gripClick);

        char msg[160];
        std::snprintf(msg, sizeof(msg), "[maratron] Activate id=%u role=%d shmMapped=%d\n",
                      unObjectId, (int)m_currentRole, (int)m_shm.Mapped());
        VRDriverLog()->Log(msg);
        return VRInitError_None;
    }

    void Deactivate() override { m_id = k_unTrackedDeviceIndexInvalid; }
    void EnterStandby() override {}
    void* GetComponent(const char*) override { return nullptr; }
    void DebugRequest(const char*, char* resp, uint32_t size) override { if (size) resp[0] = '\0'; }

    DriverPose_t GetPose() override { return MakePose(); }

    void RunFrame() {
        if (m_id == k_unTrackedDeviceIndexInvalid) return;
        float jx, jy, tr; uint32_t btn;
        m_shm.Read(jx, jy, tr, btn);  // zeros when idle/stale

        int32_t role = m_shm.Role();  // live role change (best-effort; restart is reliable)
        if (role != m_currentRole) {
            m_currentRole = role;
            VRProperties()->SetInt32Property(m_props, Prop_ControllerRoleHint_Int32, role);
        }

        auto& in = *VRDriverInput();
        in.UpdateScalarComponent(m_stickX, jx, 0.0);
        in.UpdateScalarComponent(m_stickY, jy, 0.0);
        bool moving = (std::fabs(jy) > 0.01f || std::fabs(jx) > 0.01f);
        in.UpdateBooleanComponent(m_stickTouch, moving, 0.0);
        in.UpdateBooleanComponent(m_stickClick, (btn & BTN_STICK_CLICK) != 0, 0.0);
        in.UpdateScalarComponent(m_trigger, tr, 0.0);
        in.UpdateBooleanComponent(m_gripClick, (btn & BTN_SPRINT) != 0, 0.0);

        VRServerDriverHost()->TrackedDevicePoseUpdated(m_id, MakePose(), sizeof(DriverPose_t));
    }

private:
    DriverPose_t MakePose() {
        DriverPose_t pose = {};
        // Always connected + valid so the device is visible/bindable and its joystick is
        // consumed. Harmless when idle: stick centered; a hand role only matters if set.
        pose.poseIsValid = true;
        pose.deviceIsConnected = true;
        pose.result = TrackingResult_Running_OK;
        pose.qWorldFromDriverRotation.w = 1.0;
        pose.qDriverFromHeadRotation.w = 1.0;
        pose.qRotation.w = 1.0;
        // Static resting position (slightly left, at ~hip height). Games read the
        // stick axis, not the hand pose, so a fixed pose is fine for locomotion.
        pose.vecPosition[0] = -0.2;
        pose.vecPosition[1] = 1.0;
        pose.vecPosition[2] = -0.3;
        return pose;
    }

    SharedMem& m_shm;
    TrackedDeviceIndex_t m_id = k_unTrackedDeviceIndexInvalid;
    PropertyContainerHandle_t m_props = k_ulInvalidPropertyContainer;
    VRInputComponentHandle_t m_stickX = 0, m_stickY = 0, m_stickClick = 0, m_stickTouch = 0;
    VRInputComponentHandle_t m_trigger = 0, m_trigTouch = 0, m_gripClick = 0;
    int32_t m_currentRole = 3;  // last-applied ETrackedControllerRole
};

// ---------------------------------------------------------------------------
// The server device provider.
// ---------------------------------------------------------------------------
class ServerProvider : public IServerTrackedDeviceProvider {
public:
    EVRInitError Init(IVRDriverContext* pDriverContext) override {
        VR_INIT_SERVER_DRIVER_CONTEXT(pDriverContext);
        m_shm.Open();  // ok if Python not running yet; reads return centered
        m_controller = new TreadmillController(m_shm);
        VRServerDriverHost()->TrackedDeviceAdded(
            m_controller->Serial(), TrackedDeviceClass_Controller, m_controller);
        return VRInitError_None;
    }

    void Cleanup() override {
        VR_CLEANUP_SERVER_DRIVER_CONTEXT();
        m_shm.Close();
        delete m_controller;
        m_controller = nullptr;
    }

    const char* const* GetInterfaceVersions() override { return k_InterfaceVersions; }
    void RunFrame() override { if (m_controller) m_controller->RunFrame(); }
    bool ShouldBlockStandbyMode() override { return false; }
    void EnterStandby() override {}
    void LeaveStandby() override {}

private:
    SharedMem m_shm;
    TreadmillController* m_controller = nullptr;
};

static ServerProvider g_serverProvider;

// ---------------------------------------------------------------------------
// Entry point.
// ---------------------------------------------------------------------------
extern "C" __declspec(dllexport) void* HmdDriverFactory(const char* pInterfaceName, int* pReturnCode) {
    if (std::strcmp(pInterfaceName, IServerTrackedDeviceProvider_Version) == 0) {
        return &g_serverProvider;
    }
    if (pReturnCode) *pReturnCode = VRInitError_Init_InterfaceNotFound;
    return nullptr;
}
