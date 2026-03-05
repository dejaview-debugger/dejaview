// keep this file in sync with dejaview/_memory_patch/__init__.pyi
use pyo3::ffi;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::os::raw::c_void;
use std::sync::Mutex;
use std::sync::RwLock;

// Note: These C structs were validated against Python 3.12. May break on other versions.
// Layout of CPython's PyWrapperDescrObject, needed to update d_wrapped
// so that subtypes created after patching object.__hash__ inherit hook_hash.
#[repr(C)]
struct PyDescrObject {
    ob_base: ffi::PyObject,
    d_type: *mut ffi::PyTypeObject,
    d_name: *mut ffi::PyObject,
    d_qualname: *mut ffi::PyObject,
}

#[repr(C)]
struct PyWrapperDescrObject {
    d_common: PyDescrObject,
    d_base: *const c_void,
    d_wrapped: *mut c_void,
}

struct IdTracker {
    map: HashMap<usize, u64>,
    next_id: u64,
}

impl IdTracker {
    fn get_id(&mut self, addr: usize) -> u64 {
        *self.map.entry(addr).or_insert_with(|| {
            let id = self.next_id;
            self.next_id += 1;
            id
        })
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum PatchStatus {
    Init,
    Enabled,
    Disabled,
}

struct State {
    status: PatchStatus,
    ids: Mutex<IdTracker>,
    original_alloc: ffi::PyMemAllocatorEx,
    original_id: Py<PyAny>,
    original_hash: ffi::hashfunc,
}

// Safety: raw pointers in PyMemAllocatorEx and hashfunc are only dereferenced
// under the GIL, which serializes all access.
// Also, we don't plan to support threading anyway. Even if we do, we must serialize
// all access deterministically to make replay work.
unsafe impl Send for State {}
unsafe impl Sync for State {}

static STATE: RwLock<Option<State>> = RwLock::new(None);

extern "C" fn hook_free(ctx: *mut c_void, ptr: *mut c_void) {
    let s = STATE.read().unwrap();
    let s = s.as_ref().unwrap();
    if !ptr.is_null() {
        s.ids.lock().unwrap().map.remove(&(ptr as usize));
    }
    let free_fn = s.original_alloc.free.unwrap();
    free_fn(ctx, ptr);
}

extern "C" fn hook_realloc(ctx: *mut c_void, ptr: *mut c_void, new_size: usize) -> *mut c_void {
    let s = STATE.read().unwrap();
    let s = s.as_ref().unwrap();
    let realloc_fn = s.original_alloc.realloc.unwrap();
    let new_ptr = realloc_fn(ctx, ptr, new_size);
    if !ptr.is_null() && !new_ptr.is_null() && ptr != new_ptr {
        let mut ids = s.ids.lock().unwrap();
        if let Some(id) = ids.map.remove(&(ptr as usize)) {
            ids.map.insert(new_ptr as usize, id);
        }
    }

    new_ptr
}

fn get_obj_id(obj: &Bound<'_, PyAny>) -> u64 {
    let module_name = obj
        .get_type()
        .module()
        .ok()
        .and_then(|m| m.extract::<pyo3::pybacked::PyBackedStr>().ok());

    // Skip objects from dejaview to avoid interference with debugger internal code.
    // Also skip multiprocessing.synchronize.Lock which is used during snapshotting.
    if let Some(m) = &module_name {
        if m == "multiprocessing.synchronize" || m == "dejaview" || m.starts_with("dejaview.") {
            return obj.as_ptr() as usize as u64;
        }
    }

    let addr = obj.as_ptr() as usize;
    let s = STATE.read().unwrap();
    let ids = &s.as_ref().unwrap().ids;
    ids.lock().unwrap().get_id(addr)
}

extern "C" fn hook_hash(obj: *mut ffi::PyObject) -> ffi::Py_hash_t {
    let py = unsafe { Python::assume_attached() };
    let obj = unsafe { Bound::from_borrowed_ptr(py, obj) };
    let h = get_obj_id(&obj) as ffi::Py_hash_t;
    // println!(
    //     "Getting hash for {:p}, got hash: {}, type: {}",
    //     obj.as_ptr(),
    //     h,
    //     obj.get_type(),
    // );
    if h == -1 {
        // -1 is reserved as an error value for tp_hash, so remap it to -2.
        -2
    } else {
        h
    }
}

#[pyfunction]
fn deterministic_id(obj: &Bound<'_, PyAny>) -> PyResult<u64> {
    let id = get_obj_id(obj);
    // println!(
    //     "Getting ID for {:p}, got ID: {}, type: {}",
    //     obj.as_ptr(),
    //     id,
    //     obj.get_type(),
    // );
    Ok(id)
}

#[pyfunction]
fn enable(py: Python) -> PyResult<()> {
    // Gather originals without holding the lock
    let builtins = PyModule::import(py, "builtins")?;
    let original_id = builtins.getattr("id")?.unbind();
    let object_type = builtins.getattr("object")?;
    let type_ptr = object_type.as_ptr() as *mut ffi::PyTypeObject;
    let hash_descr = object_type.getattr("__hash__")?;
    let original_hash = unsafe { (*type_ptr).tp_hash.unwrap() };
    let new_id_func = wrap_pyfunction!(deterministic_id, py)?;

    let original_alloc = unsafe {
        let mut alloc = std::mem::zeroed::<ffi::PyMemAllocatorEx>();
        ffi::PyMem_GetAllocator(ffi::PyMemAllocatorDomain::PYMEM_DOMAIN_OBJ, &mut alloc);
        alloc
    };

    // Initialize state
    {
        let mut s = STATE.write().unwrap();

        if s.is_some() && s.as_ref().unwrap().status != PatchStatus::Disabled {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "_memory_patch is already enabled",
            ));
        }

        // Keep old IdTracker if already initialized to preserve IDs
        let ids = if let Some(old) = s.take() {
            old.ids
        } else {
            Mutex::new(IdTracker {
                map: HashMap::new(),
                next_id: 1,
            })
        };

        *s = Some(State {
            status: PatchStatus::Init,
            ids,
            original_alloc,
            original_id,
            original_hash,
        });
    }

    // Hook free and realloc
    let mut hook_alloc = ffi::PyMemAllocatorEx {
        ctx: original_alloc.ctx,
        malloc: original_alloc.malloc,
        calloc: original_alloc.calloc,
        realloc: Some(hook_realloc),
        free: Some(hook_free),
    };
    unsafe {
        ffi::PyMem_SetAllocator(ffi::PyMemAllocatorDomain::PYMEM_DOMAIN_OBJ, &mut hook_alloc);
    }

    // Patch built-in id()
    builtins.setattr("id", new_id_func)?;

    // Patch object.__hash__
    unsafe {
        (*type_ptr).tp_hash = Some(hook_hash);
        let wrapper = hash_descr.as_ptr() as *mut PyWrapperDescrObject;
        assert!(
            (*wrapper).d_wrapped == original_hash as *mut c_void,
            "Unexpected object.__hash__ wrapper layout."
        );
        (*wrapper).d_wrapped = hook_hash as *mut c_void;
        ffi::PyType_Modified(type_ptr);
    }

    // Mark as enabled after all hooks are in place
    STATE.write().unwrap().as_mut().unwrap().status = PatchStatus::Enabled;

    Ok(())
}

#[pyfunction]
fn disable(py: Python) -> PyResult<()> {
    // Pop state
    let mut guard = STATE.write().unwrap();

    if guard.is_none() || guard.as_ref().unwrap().status != PatchStatus::Enabled {
        return Err(pyo3::exceptions::PyRuntimeError::new_err(
            "_memory_patch is not enabled",
        ));
    }

    let s = guard.as_mut().unwrap();
    s.status = PatchStatus::Disabled;
    s.ids.lock().unwrap().map.clear();

    // Restore original allocator
    unsafe {
        ffi::PyMem_SetAllocator(
            ffi::PyMemAllocatorDomain::PYMEM_DOMAIN_OBJ,
            &mut s.original_alloc,
        );
    }

    // Restore original id()
    let builtins = PyModule::import(py, "builtins")?;
    builtins.setattr("id", s.original_id.bind(py))?;

    // Restore original object.__hash__
    let object_type = builtins.getattr("object")?;
    let type_ptr = object_type.as_ptr() as *mut ffi::PyTypeObject;
    let hash_descr = object_type.getattr("__hash__")?;
    unsafe {
        (*type_ptr).tp_hash = Some(s.original_hash);
        let wrapper = hash_descr.as_ptr() as *mut PyWrapperDescrObject;
        (*wrapper).d_wrapped = s.original_hash as *mut c_void;
        ffi::PyType_Modified(type_ptr);
    }

    Ok(())
}

// Module Initialization
#[pymodule]
fn _memory_patch(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(enable, m)?)?;
    m.add_function(wrap_pyfunction!(disable, m)?)?;
    m.add_function(wrap_pyfunction!(deterministic_id, m)?)?;
    Ok(())
}
