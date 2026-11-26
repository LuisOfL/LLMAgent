import sys
import os
import ctypes

def main():
    engine_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine")
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(engine_dir)
        except Exception as e:
            sys.stderr.write(f"Warning add_dll_directory: {e}\n")
    
    os.environ["PATH"] = engine_dir + os.pathsep + os.environ.get("PATH", "")
    
    # 1. Registrar backends en ggml.dll antes de invocar el servidor
    try:
        ggml_dll = ctypes.CDLL(os.path.join(engine_dir, "ggml.dll"))
        register_fn = getattr(ggml_dll, "ggml_backend_register")
        register_fn.argtypes = [ctypes.c_void_p]
        register_fn.restype = None

        # Registrar CUDA (GPU NVIDIA RTX 4050)
        cuda_path = os.path.join(engine_dir, "ggml-cuda.dll")
        if os.path.exists(cuda_path):
            try:
                cuda_dll = ctypes.CDLL(cuda_path)
                cuda_reg_fn = getattr(cuda_dll, "ggml_backend_cuda_reg")
                cuda_reg_fn.restype = ctypes.c_void_p
                register_fn(cuda_reg_fn())
            except Exception as e:
                sys.stderr.write(f"Warning cargando CUDA backend: {e}\n")

        # Registrar el backend CPU óptimo según el procesador
        best_cpu = None
        best_score = -1
        for f in os.listdir(engine_dir):
            if f.startswith("ggml-cpu") and f.endswith(".dll"):
                try:
                    d = ctypes.CDLL(os.path.join(engine_dir, f))
                    score_fn = getattr(d, "ggml_backend_score", None)
                    reg_fn = getattr(d, "ggml_backend_cpu_reg", None)
                    if score_fn and reg_fn:
                        score_fn.restype = ctypes.c_int
                        s = score_fn()
                        if s > best_score:
                            best_score = s
                            best_cpu = (f, reg_fn)
                except Exception:
                    pass
                    
        if best_cpu:
            best_cpu[1].restype = ctypes.c_void_p
            register_fn(best_cpu[1]())
    except Exception as e:
        sys.stderr.write(f"Warning en registro de backends: {e}\n")

    # 2. Cargar llama-server-impl.dll
    dll_path = os.path.join(engine_dir, "llama-server-impl.dll")
    if not os.path.exists(dll_path):
        sys.stderr.write(f"Error: DLL no encontrada: {dll_path}\n")
        sys.exit(1)
        
    try:
        dll = ctypes.CDLL(dll_path)
    except Exception as e:
        sys.stderr.write(f"Error cargando DLL: {e}\n")
        sys.exit(1)
        
    try:
        fn = getattr(dll, "?llama_server@@YAHHPEAPEAD@Z")
    except AttributeError:
        sys.stderr.write("Error: Simbolo ?llama_server@@YAHHPEAPEAD@Z no encontrado en DLL\n")
        sys.exit(1)
        
    fn.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
    fn.restype = ctypes.c_int
    
    # argv[0] debe ser el nombre del ejecutable
    raw_args = ["llama-server"] + sys.argv[1:]
    encoded_args = [arg.encode("utf-8") for arg in raw_args]
    argc = len(encoded_args)
    argv = (ctypes.c_char_p * argc)(*encoded_args)
    
    # Llamar a llama_server (se ejecuta en este proceso)
    res = fn(argc, argv)
    sys.exit(res)

if __name__ == "__main__":
    main()
