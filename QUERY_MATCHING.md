
context: You are a experienced solution architect designing enterprise grade systems . Implement entire AWS architecture 


  current scenario:

- you have an excel file containing two coloumns with extremely large queries  tquerying to ORACLE and SNOWFLAKE databases 
- a python script reads these queries and run them on the databases and then matches the result.
- each query is about 50 characters big, extremely large queries 
- we must see that the values returned must be consistent between the two databases


   task 

- entire workflow should be shifted to AWS CLOUD 
- base image of python willl be pulled, dependencies will installed in the build phase and be pushed into HARBOR or any similar service 
- step functions will be involved as query runtime is high 
- python script will be run on clusters which run the image pulled from harbor
- it should be automatided and jobs will be run everyday 
- design a indepth cloud architecture that optimises operational overhead
- containers run image as there are custom built images which have the necessary dependencies 
- operational overhead should be minimised 
- jobs will be run incosistently so on demand pricing is recommended 


   GOAL:

- make enterprise grade workflow , use best optimal services 
- company is open to monolithic architecture entirely on AWS 
- latency and querying speed should be prioritised , the system should be well optmised and fast while operational overhead should be minimal 
- create multiple architecture indepth scenaros and solutions , and give indepth explaination of benifits and tradoffs of the architectures and decisions made