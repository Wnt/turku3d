import { randomInt } from 'node:crypto';

// Every cross-product pair is friendly, ASCII-only and at most 16 characters.
export const ADJECTIVES = `Turbo Sneaky Happy Sunny Cozy Merry Jolly Mellow Swift Zippy
Bouncy Breezy Bright Calm Clever Cloudy Cosmic Crispy Dapper Dashing Dreamy Eager
Fancy Fluffy Flying Friendly Frosty Gentle Gleaming Golden Grand Green Groovy Handy
Icy Jazzy Jumpy Kindly Leafy Light Little Lucky Magic Mighty Minty Misty Neat Nifty
Nimble Orange Peachy Peppy Perky Plucky Puffy Quick Quiet Rapid Rosy Shiny Silky
Silver Smooth Snappy Snowy Soft Sparkly Speedy Spry Starry Steady Super Tidy Tiny
Toasty Wavy Whimsy Zesty`.split(/\s+/);
export const NOUNS = `Herring Pizza Scooter Driver Ferry River Bridge Castle Market Harbor
Baker Berry Biscuit Breeze Bun Cabin Carrot Cherry Cloud Clover Cookie Courier
Cruiser Daisy Dolphin Donut Dumpling Falcon Feather Fern Finch Fox Garden Gecko
Glider Gull Hazel Hedgehog Heron Hill Juniper Kite Kiwi Lantern Lark Leaf Lemon
Lime Lynx Maple Meadow Melon Moose Muffin Noodle Otter Owl Panda Pancake Peach
Pear Pebble Penguin Pepper Pine Puffin Rabbit Robin Rocket Rowan Sail Salmon
Seal Sparrow Sprout Squirrel Star Taxi Toast Tram Turtle Waffle Willow Wren`.split(/\s+/);
export const NICKS = ADJECTIVES.flatMap(adjective => NOUNS.map(noun => adjective + noun));
export const validNickname = value => typeof value === 'string' && value.length <= 16
  && /^[A-Z][a-z]+[A-Z][a-z]+$/.test(value);

export function nickname(used, previous, random = randomInt) {
  const choices = NICKS.filter(n => !used.has(n) && n !== previous);
  if (!choices.length) return null;
  return choices[random(choices.length)];
}
