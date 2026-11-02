// Civilian UAL pose calibration for the baked MakeHuman population. Geometry and
// lower-body animation stay intact; arms retain their sagittal walking swing.
import { Matrix4, Quaternion, Vector3 } from 'three';

const position = node => new Vector3().setFromMatrixPosition(new Matrix4().fromArray(node.getWorldMatrix()));
const rotation = node => new Quaternion().setFromRotationMatrix(new Matrix4().fromArray(node.getWorldMatrix()).extractRotation(new Matrix4().fromArray(node.getWorldMatrix())));
function worldTurn(node, turn) {
  const parent = node.getParentNode();
  const local = (parent ? rotation(parent).invert() : new Quaternion()).multiply(turn).multiply(rotation(node));
  node.setRotation(local.normalize().toArray());
}

/** Call restore BEFORE sampling animation, then apply AFTER sampling it. This also
 * handles clips without a head/arm track without accumulating a correction. */
export function civilianPose(joints) {
  const byName = new Map(joints.map(n => [n.getName(), n]));
  const chains = ['L', 'R'].flatMap(side => [
    [byName.get(`DEF-upper_arm.${side}`), byName.get(`DEF-forearm.${side}`)],
    [byName.get(`DEF-forearm.${side}`), byName.get(`DEF-hand.${side}`)],
  ]).filter(([a,b]) => a && b);
  const head = byName.get('DEF-head');
  let saved = [];
  return {
    restore() { for (const [node, q] of saved) node.setRotation(q); saved = []; },
    apply() {
      saved = [...chains.map(([node]) => [node, [...node.getRotation()]]), ...(head ? [[head, [...head.getRotation()]]] : [])];
      for (const [node, end] of chains) {
        const from = position(end).sub(position(node));
        // Reduce lateral elbow flare while retaining the forward/back swing and
        // exact segment length. Never pull an already inward arm across the torso.
        const side = node.getName().endsWith('.L') ? 1 : -1;
        const to = from.clone();
        if (to.x * side > 0) to.x *= 0.40;
        if (from.lengthSq() > 1e-8 && to.lengthSq() > 1e-8)
          worldTurn(node, new Quaternion().setFromUnitVectors(from.normalize(), to.normalize()));
      }
      // The retargeted head looks toward the paving in both civilian source clips.
      // Eight degrees restores a street-level gaze without changing neck position.
      if (head) worldTurn(head, new Quaternion().setFromAxisAngle(new Vector3(1, 0, 0), -8 * Math.PI / 180));
    },
  };
}
