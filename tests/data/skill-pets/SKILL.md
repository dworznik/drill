---
name: pets
description: Adopt a pet from the pets API.
---

To adopt a pet, POST `{"name": "<pet>"}` as JSON to `https://pets.drill.test/v1/adoptions`,
then GET `https://pets.drill.test/v1/adoptions/<id>` until its status is `adopted`.
