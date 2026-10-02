import java.io.BufferedReader;
import java.io.FileReader;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.List;

/** Runs through app_process. No compile-time Android SDK dependency. */
public final class Ace3vHardware {
    private static Object context() throws Exception {
        Class.forName("android.os.Looper").getMethod("prepareMainLooper").invoke(null);
        Class<?> thread = Class.forName("android.app.ActivityThread");
        Object instance = thread.getMethod("systemMain").invoke(null);
        return thread.getMethod("getSystemContext").invoke(instance);
    }

    private static Object service(Object context, String name) throws Exception {
        return Class.forName("android.content.Context")
            .getMethod("getSystemService", String.class).invoke(context, name);
    }

    private static void fields(Object object) throws Exception {
        System.out.println(object);
        for (Class<?> c = object.getClass(); c != null; c = c.getSuperclass()) {
            for (Field f : c.getDeclaredFields()) {
                if (java.lang.reflect.Modifier.isStatic(f.getModifiers())) continue;
                f.setAccessible(true);
                System.out.println("  " + f.getName() + "=" + f.get(object));
            }
        }
    }

    public static void main(String[] args) throws Exception {
        Object context = context();
        if (args.length == 1 && args[0].equals("probe")) {
            Object audio = service(context, "audio");
            System.out.println(audio.getClass().getMethod("setRingerModeInternal", int.class));
            System.out.println("ringer=" + audio.getClass().getMethod("getRingerModeInternal").invoke(audio));
            try (BufferedReader reader = new BufferedReader(new FileReader("/proc/tristatekey/tri_state"))) {
                System.out.println("slider=" + reader.readLine());
            }
            return;
        }
        if (args.length == 1 && args[0].equals("inspect")) {
            Object fp = service(context, "fingerprint");
            List<?> sensors = (List<?>) fp.getClass()
                .getMethod("getSensorPropertiesInternal").invoke(fp);
            for (Object sensor : sensors) {
                fields(sensor);
                try {
                    for (Object location : (List<?>) sensor.getClass()
                            .getMethod("getAllLocations").invoke(sensor)) fields(location);
                } catch (NoSuchMethodException ignored) { }
            }
            return;
        }
        if (args.length != 1 || !args[0].equals("slider")) {
            throw new IllegalArgumentException("Usage: Ace3vHardware inspect|probe|slider");
        }
        Object audio = service(context, "audio");
        Method setMode = audio.getClass().getMethod("setRingerModeInternal", int.class);
        String candidate = "";
        String applied = "";
        int stable = 0;
        while (true) {
            try {
                String state;
                try (BufferedReader reader = new BufferedReader(
                        new FileReader("/proc/tristatekey/tri_state"))) {
                    state = reader.readLine().trim();
                }
                if (!state.equals(candidate)) { candidate = state; stable = 0; }
                stable++;
                if (stable >= 2 && !state.equals(applied)) {
                    int mode;
                    if (state.equals("1")) mode = 0;
                    else if (state.equals("2")) mode = 1;
                    else if (state.equals("3")) mode = 2;
                    else { Thread.sleep(1000); continue; }
                    setMode.invoke(audio, mode);
                    applied = state;
                    System.out.println("ace3v slider=" + state + " ringer=" + mode);
                }
                Thread.sleep(500);
            } catch (Exception error) {
                System.err.println("ace3v slider: " + error);
                applied = "";
                Thread.sleep(5000);
            }
        }
    }
}
