// QA cameras omit play-mode actors. Restore the native parked fleet from streamed records.
export async function installParked(page, shot) {
  return page.evaluate(async shot => {
    const v = window.__video, r = window.__render, g = window.__game;
    const {ParkedCarVisuals} = await import('/src/actors/car.ts');
    const slots = await import('/src/world/parkedVehicles.ts');
    const modern = typeof slots.slotRotation === 'function';
    const manifest = window.__videoCoarse.host.manifest;
    let cars = (manifest.vehicles ?? []).filter(car => car.model !== 'truck');
    if (modern) {
      const {parkedNight, slotShown} = await import('/src/world/parkedOccupancy.ts');
      const night = parkedNight(v.baseDate, () => shot.hour);
      cars = cars.filter(car => slotShown(car, night));
    }
    v.parked ??= new ParkedCarVisuals(r.environment.scene);
    await v.parked.load({...manifest, vehicles: cars});
    v.parkedLegacy = !modern;
    v.parkedStates = cars.map(car => {
      const yaw = (car.yaw - Math.PI / 2) / 2;
      return {id: car.object_id, pos: {x: car.x, y: car.y + .75, z: car.z},
        rot: modern ? slots.slotRotation(car) : {x: 0, y: Math.sin(yaw), z: 0, w: Math.cos(yaw)},
        speed: 0, wheels: []};
    });
    v.updateParked = () => {
      v.parked.setView?.(r.camera, g.dayClock.night);
      // Published play mode only spawns nearby parked bodies; Gen 2 also draws distant resting slots.
      const states = v.parkedLegacy ? v.parkedStates.filter(s =>
        Math.hypot(s.pos.x-r.camera.position.x,s.pos.z-r.camera.position.z) <= 120) : v.parkedStates;
      v.parked.update(states);
    };
    v.updateParked();
    await r.renderer.compileAsync(r.environment.scene, r.camera);
    return {records: cars.length, nativeOccupancy: modern, unsupportedModels: ['truck'],
      ids: cars.map(car => car.object_id).sort()};
  }, shot);
}
